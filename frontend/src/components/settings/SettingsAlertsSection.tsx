import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api, formatApiError } from '../../api';
import {
  DEFAULT_TELEMETRY_ALERT_RULES,
  TELEMETRY_ALERT_RULE_IDS,
  isEmailDestinationReady,
  isWebhookDestinationReady,
  normalizeTelemetryAlertCatalog,
  resolveNotificationDestinations,
  resolveTelemetryAlertRules,
  type AppSettings,
  type AppSettingsUpdate,
  type Contact,
  type TelemetryAlertCatalog,
  type TelemetryAlertCatalogMetric,
  type TelemetryAlertNodeOverride,
  type TelemetryAlertRuleOverrideSpec,
  type TelemetryAlertRuleSpec,
  type TelemetryAlertRules,
  type TelemetryAlertTrackedNode,
} from '../../types';
import { formatTime } from '../../utils/messageParser';
import { getContactDisplayName } from '../../utils/pubkey';
import { LPP_UNIT_MAP } from '../repeater/repeaterPaneShared';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../ui/dialog';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Separator } from '../ui/separator';
import { toast } from '../ui/sonner';
import { SettingsGroupHeader } from './settingsPrimitives';

const BUILTIN_UNITS: Record<string, string> = {
  battery: 'V',
  noise: 'dBm',
  rssi: 'dBm',
  snr: 'dB',
  tx_queue: '',
  silence: '',
  gps_lost: '',
};

function ruleI18nId(id: string): string {
  if (!id.startsWith('lpp:')) return id;
  return id.slice(4).split(':')[0] ?? id;
}

function lppTypeFromRuleId(id: string): string | null {
  if (!id.startsWith('lpp:')) return null;
  return id.slice(4).split(':')[0] ?? null;
}

function metricIdsFromCatalog(catalog: TelemetryAlertCatalog | null): string[] {
  const ids: string[] = [...TELEMETRY_ALERT_RULE_IDS];
  for (const metric of catalog?.metrics ?? []) {
    // Channel-specific latch ids (lpp:temperature:1) are not global threshold rows.
    if (!metric.id || metric.id.split(':').length > 2) continue;
    if (!ids.includes(metric.id)) {
      ids.push(metric.id);
    }
  }
  return ids;
}

function catalogMetricMap(
  catalog: TelemetryAlertCatalog | null
): Record<string, TelemetryAlertCatalogMetric> {
  const map: Record<string, TelemetryAlertCatalogMetric> = {};
  for (const metric of catalog?.metrics ?? []) {
    map[metric.id] = metric;
  }
  return map;
}

function ruleHasThreshold(spec: TelemetryAlertRuleSpec | undefined): boolean {
  return spec?.threshold !== undefined || spec?.op !== undefined;
}

export function SettingsAlertsSection({
  appSettings,
  onSaveAppSettings,
  contacts = [],
  trackedTelemetryRepeaters = [],
  trackedTelemetryContacts = [],
  className,
}: {
  appSettings: AppSettings;
  onSaveAppSettings: (update: AppSettingsUpdate) => Promise<void>;
  contacts?: Contact[];
  trackedTelemetryRepeaters?: string[];
  trackedTelemetryContacts?: string[];
  className?: string;
}) {
  const { t } = useTranslation();
  const [catalog, setCatalog] = useState<TelemetryAlertCatalog | null>(null);
  const [overrideKey, setOverrideKey] = useState<string | null>(null);
  const rules = resolveTelemetryAlertRules(appSettings.telemetry_alert_rules);
  const dest = resolveNotificationDestinations(appSettings.notification_destinations);
  const rulesRef = useRef(rules);
  const saveChainRef = useRef<Promise<void>>(Promise.resolve());

  useEffect(() => {
    rulesRef.current = rules;
  }, [rules]);

  useEffect(() => {
    let cancelled = false;
    void api.getTelemetryAlertCatalog().then(
      (raw) => {
        if (!cancelled) setCatalog(normalizeTelemetryAlertCatalog(raw));
      },
      () => {
        if (!cancelled) setCatalog({ metrics: [], latches: [], tracked: [] });
      }
    );
    return () => {
      cancelled = true;
    };
  }, [trackedTelemetryRepeaters, trackedTelemetryContacts]);

  const persistRules = (next: TelemetryAlertRules, revert: () => void): Promise<void> => {
    const previous = rulesRef.current;
    rulesRef.current = next;
    const overridesChanged = JSON.stringify(previous.overrides) !== JSON.stringify(next.overrides);
    const chained = saveChainRef.current.then(async () => {
      try {
        await onSaveAppSettings({
          telemetry_alert_rules: overridesChanged
            ? next
            : ({ channels: next.channels, rules: next.rules } as TelemetryAlertRules),
        });
      } catch (err) {
        console.error('Failed to save telemetry alert rules:', err);
        rulesRef.current = previous;
        revert();
        toast.error(t('settings.alerts.saveFailed'), {
          description: formatApiError(err, t) || t('settings.alerts.unknownError'),
        });
      }
    });
    saveChainRef.current = chained;
    return chained;
  };

  const patchRules = (updater: (current: TelemetryAlertRules) => TelemetryAlertRules) => {
    const previous = rulesRef.current;
    const next = updater(previous);
    void persistRules(next, () => {
      rulesRef.current = previous;
    });
  };

  const emailReady = isEmailDestinationReady(dest.email);
  const webhookReady = isWebhookDestinationReady(dest.webhook);
  const metrics = catalogMetricMap(catalog);
  const ruleIds = metricIdsFromCatalog(catalog);

  const polledNodes = useMemo<TelemetryAlertTrackedNode[]>(() => {
    if (catalog?.tracked && catalog.tracked.length > 0) return catalog.tracked;
    const keys = [...trackedTelemetryRepeaters, ...trackedTelemetryContacts];
    const seen = new Set<string>();
    const nodes: TelemetryAlertTrackedNode[] = [];
    for (const key of keys) {
      const normalized = key.toLowerCase();
      if (seen.has(normalized)) continue;
      seen.add(normalized);
      const contact = contacts.find((c) => c.public_key.toLowerCase() === normalized);
      nodes.push({
        public_key: key,
        name: contact ? getContactDisplayName(contact.name, contact.public_key) : key.slice(0, 12),
        alerting: rules.overrides[normalized]?.alerting ?? true,
      });
    }
    return nodes;
  }, [catalog, contacts, rules.overrides, trackedTelemetryContacts, trackedTelemetryRepeaters]);

  const trackedKeys = useMemo(
    () => new Set(polledNodes.map((node) => node.public_key.toLowerCase())),
    [polledNodes]
  );

  const latches = (catalog?.latches ?? []).filter((latch) =>
    trackedKeys.has(latch.public_key.toLowerCase())
  );

  const overrideNode = overrideKey
    ? (polledNodes.find((node) => node.public_key === overrideKey) ?? null)
    : null;

  const nodeName = (key: string, fallback?: string) => {
    const contact = contacts.find((c) => c.public_key.toLowerCase() === key.toLowerCase());
    if (contact) return getContactDisplayName(contact.name, contact.public_key);
    const tracked = polledNodes.find((node) => node.public_key.toLowerCase() === key.toLowerCase());
    return tracked?.name || fallback || key.slice(0, 12);
  };

  const ruleLabel = (id: string) => {
    const metric = metrics[id];
    if (metric?.label_key) return t(metric.label_key, { defaultValue: id });
    const i18nId = ruleI18nId(id);
    return t(`settings.alerts.rules.${i18nId}.label`, {
      defaultValue: i18nId.charAt(0).toUpperCase() + i18nId.slice(1).replace(/_/g, ' '),
    });
  };

  const ruleHelp = (id: string) =>
    t(`settings.alerts.rules.${ruleI18nId(id)}.help`, { defaultValue: '' });

  const ruleUnit = (id: string) => {
    const lppType = lppTypeFromRuleId(id);
    return (
      metrics[id]?.unit ?? (lppType ? LPP_UNIT_MAP[lppType] : undefined) ?? BUILTIN_UNITS[id] ?? ''
    );
  };

  const nodeAlerting = (node: TelemetryAlertTrackedNode) => {
    const override = rules.overrides[node.public_key.toLowerCase()];
    if (override?.alerting != null) return override.alerting;
    return node.alerting;
  };

  return (
    <div className={className}>
      <div className="space-y-3">
        <SettingsGroupHeader title={t('settings.alerts.voices')} storedOn="server" instant />
        <p className="text-[0.8125rem] text-muted-foreground">{t('settings.alerts.voicesHelp')}</p>
        <VoiceRow
          id="push"
          label={t('settings.alerts.voicePush')}
          help={t('settings.alerts.voicePushHelp')}
          checked={rules.channels.push}
          onCheckedChange={(checked) =>
            patchRules((current) => ({
              ...current,
              channels: { ...current.channels, push: checked },
            }))
          }
        />
        <VoiceRow
          id="email"
          label={t('settings.alerts.voiceEmail')}
          help={t('settings.alerts.voiceEmailHelp')}
          checked={rules.channels.email}
          disabled={!emailReady}
          configureHref={!emailReady ? '#settings/notifications' : undefined}
          configureLabel={t('settings.alerts.configureDestinations')}
          onCheckedChange={(checked) =>
            patchRules((current) => ({
              ...current,
              channels: { ...current.channels, email: checked },
            }))
          }
        />
        <VoiceRow
          id="webhook"
          label={t('settings.alerts.voiceWebhook')}
          help={t('settings.alerts.voiceWebhookHelp')}
          checked={rules.channels.webhook}
          disabled={!webhookReady}
          configureHref={!webhookReady ? '#settings/notifications' : undefined}
          configureLabel={t('settings.alerts.configureDestinations')}
          onCheckedChange={(checked) =>
            patchRules((current) => ({
              ...current,
              channels: { ...current.channels, webhook: checked },
            }))
          }
        />
      </div>

      <Separator />

      <div className="space-y-3">
        <SettingsGroupHeader title={t('settings.alerts.globalRules')} storedOn="server" instant />
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.alerts.globalRulesHelp')}
        </p>
        <div className="space-y-3">
          {ruleIds.map((id) => {
            const spec = rules.rules[id] ??
              DEFAULT_TELEMETRY_ALERT_RULES.rules[id] ?? {
                enabled: false,
              };
            const help = ruleHelp(id);
            return (
              <div
                key={id}
                className="flex items-start gap-3 rounded-md border border-border/60 p-3"
              >
                <Checkbox
                  id={`alert-rule-${id}`}
                  checked={spec.enabled}
                  onCheckedChange={(checked) =>
                    patchRules((current) => ({
                      ...current,
                      rules: {
                        ...current.rules,
                        [id]: { ...spec, ...current.rules[id], enabled: checked === true },
                      },
                    }))
                  }
                  className="mt-0.5"
                />
                <div className="min-w-0 flex-1 space-y-1.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <Label htmlFor={`alert-rule-${id}`}>{ruleLabel(id)}</Label>
                    {ruleHasThreshold(spec) && (
                      <>
                        <Input
                          id={`alert-rule-${id}-threshold`}
                          type="number"
                          step="any"
                          inputMode="decimal"
                          aria-label={t('settings.alerts.thresholdAria', { name: ruleLabel(id) })}
                          defaultValue={spec.threshold ?? ''}
                          key={`${id}-${spec.threshold ?? ''}`}
                          onBlur={(event) => {
                            const raw = event.target.value.trim();
                            if (raw === '') {
                              event.target.value =
                                spec.threshold != null ? String(spec.threshold) : '';
                              return;
                            }
                            const parsed = Number(raw);
                            if (!Number.isFinite(parsed) || parsed === spec.threshold) {
                              event.target.value =
                                spec.threshold != null ? String(spec.threshold) : '';
                              return;
                            }
                            patchRules((current) => ({
                              ...current,
                              rules: {
                                ...current.rules,
                                [id]: { ...spec, ...current.rules[id], threshold: parsed },
                              },
                            }));
                          }}
                          className="w-24"
                        />
                        {ruleUnit(id) ? (
                          <span className="text-sm text-muted-foreground">{ruleUnit(id)}</span>
                        ) : null}
                      </>
                    )}
                  </div>
                  {help ? <p className="text-[0.8125rem] text-muted-foreground">{help}</p> : null}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <Separator />

      <div className="space-y-3">
        <SettingsGroupHeader title={t('settings.alerts.polledNodes')} storedOn="server" instant />
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.alerts.polledNodesHelp')}
        </p>
        {polledNodes.length === 0 ? (
          <p className="text-sm text-muted-foreground italic">
            {t('settings.alerts.noPolledNodes')}
          </p>
        ) : (
          <div className="space-y-2">
            {polledNodes.map((node) => {
              const alerting = nodeAlerting(node);
              return (
                <div
                  key={node.public_key}
                  className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2"
                >
                  <label className="flex min-w-0 flex-1 items-start gap-3 cursor-pointer">
                    <Checkbox
                      checked={alerting}
                      onCheckedChange={(checked) =>
                        patchRules((current) => {
                          const key = node.public_key.toLowerCase();
                          const existing = current.overrides[key] ?? {};
                          return {
                            ...current,
                            overrides: {
                              ...current.overrides,
                              [key]: { ...existing, alerting: checked === true },
                            },
                          };
                        })
                      }
                      className="mt-0.5"
                    />
                    <div className="min-w-0">
                      <span className="block truncate text-sm">
                        {nodeName(node.public_key, node.name)}
                      </span>
                      <span className="text-[0.625rem] font-mono text-muted-foreground">
                        {node.public_key.slice(0, 12)}
                      </span>
                    </div>
                  </label>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setOverrideKey(node.public_key)}
                  >
                    {t('settings.alerts.override')}
                  </Button>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <Separator />

      <div className="space-y-3">
        <SettingsGroupHeader title={t('settings.alerts.latches')} storedOn="server" instant />
        <p className="text-[0.8125rem] text-muted-foreground">{t('settings.alerts.latchesHelp')}</p>
        {latches.length === 0 ? (
          <p className="text-sm text-muted-foreground italic">{t('settings.alerts.noLatches')}</p>
        ) : (
          <div className="space-y-2">
            {latches.map((latch) => (
              <div
                key={`${latch.public_key}-${latch.rule_id}-${latch.last_fired_at}`}
                className="rounded-md border border-border px-3 py-2"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium">{nodeName(latch.public_key)}</span>
                  <span className="text-[0.625rem] uppercase tracking-wider px-1.5 py-0.5 rounded bg-primary/10 text-primary">
                    {ruleLabel(latch.rule_id)}
                  </span>
                </div>
                <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-muted-foreground">
                  {latch.last_value != null && (
                    <span>
                      {t('settings.alerts.latchValue', {
                        value: latch.last_value,
                        unit: ruleUnit(latch.rule_id),
                      })}
                    </span>
                  )}
                  {latch.last_fired_at > 0 && (
                    <span>
                      {t('settings.alerts.latchFired', { time: formatTime(latch.last_fired_at) })}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {overrideNode && (
        <NodeOverrideModal
          node={overrideNode}
          nodeLabel={nodeName(overrideNode.public_key, overrideNode.name)}
          ruleIds={ruleIds}
          rules={rules}
          ruleLabel={ruleLabel}
          ruleUnit={ruleUnit}
          onClose={() => setOverrideKey(null)}
          onChange={(nextOverride) =>
            patchRules((current) => {
              const key = overrideNode.public_key.toLowerCase();
              const overrides = { ...current.overrides };
              if (
                nextOverride.alerting == null &&
                (!nextOverride.rules || Object.keys(nextOverride.rules).length === 0)
              ) {
                delete overrides[key];
              } else {
                overrides[key] = nextOverride;
              }
              return { ...current, overrides };
            })
          }
        />
      )}
    </div>
  );
}

function VoiceRow({
  id,
  label,
  help,
  checked,
  disabled,
  configureHref,
  configureLabel,
  onCheckedChange,
}: {
  id: string;
  label: string;
  help: string;
  checked: boolean;
  disabled?: boolean;
  configureHref?: string;
  configureLabel?: string;
  onCheckedChange: (checked: boolean) => void;
}) {
  return (
    <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
      <Checkbox
        id={`alert-voice-${id}`}
        checked={checked}
        disabled={disabled}
        onCheckedChange={(value) => onCheckedChange(value === true)}
        className="mt-0.5"
      />
      <div className="space-y-1">
        <Label htmlFor={`alert-voice-${id}`}>{label}</Label>
        <p className="text-[0.8125rem] text-muted-foreground">{help}</p>
        {configureHref && (
          <a
            href={configureHref}
            className="inline-block text-[0.8125rem] underline text-primary hover:text-primary/80 transition-colors"
          >
            {configureLabel}
          </a>
        )}
      </div>
    </div>
  );
}

function NodeOverrideModal({
  node,
  nodeLabel,
  ruleIds,
  rules,
  ruleLabel,
  ruleUnit,
  onClose,
  onChange,
}: {
  node: TelemetryAlertTrackedNode;
  nodeLabel: string;
  ruleIds: string[];
  rules: TelemetryAlertRules;
  ruleLabel: (id: string) => string;
  ruleUnit: (id: string) => string;
  onClose: () => void;
  onChange: (override: TelemetryAlertNodeOverride) => void;
}) {
  const { t } = useTranslation();
  const key = node.public_key.toLowerCase();
  const override = rules.overrides[key] ?? {};

  const resolved = (id: string) => {
    const global = rules.rules[id] ?? DEFAULT_TELEMETRY_ALERT_RULES.rules[id] ?? { enabled: false };
    const local = override.rules?.[id];
    return {
      enabled: local?.enabled ?? global.enabled,
      threshold: local?.threshold ?? global.threshold,
      global,
      overridden: local != null,
    };
  };

  const writeRule = (id: string, patch: TelemetryAlertRuleOverrideSpec) => {
    const { global } = resolved(id);
    const enabled = patch.enabled ?? override.rules?.[id]?.enabled ?? global.enabled;
    const threshold = patch.threshold ?? override.rules?.[id]?.threshold ?? global.threshold;
    const nextRules = { ...(override.rules ?? {}) };
    const sameEnabled = enabled === global.enabled;
    const sameThreshold = threshold === global.threshold;
    if (sameEnabled && sameThreshold) {
      delete nextRules[id];
    } else {
      nextRules[id] = {
        ...(sameEnabled ? {} : { enabled }),
        ...(sameThreshold || threshold === undefined ? {} : { threshold }),
      };
    }
    onChange({
      ...override,
      rules: Object.keys(nextRules).length > 0 ? nextRules : undefined,
    });
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('settings.alerts.overrideTitle', { name: nodeLabel })}</DialogTitle>
          <DialogDescription>{t('settings.alerts.overrideHelp')}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          {ruleIds.map((id) => {
            const spec = resolved(id);
            const global = spec.global;
            return (
              <div
                key={id}
                className="flex items-start gap-3 rounded-md border border-border/60 p-3"
              >
                <Checkbox
                  id={`override-rule-${id}`}
                  checked={spec.enabled}
                  onCheckedChange={(checked) => writeRule(id, { enabled: checked === true })}
                  className="mt-0.5"
                />
                <div className="min-w-0 flex-1 space-y-1.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <Label htmlFor={`override-rule-${id}`}>{ruleLabel(id)}</Label>
                    {spec.overridden && (
                      <span className="text-[0.625rem] uppercase tracking-wider px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
                        {t('settings.alerts.overridden')}
                      </span>
                    )}
                    {ruleHasThreshold(global) && (
                      <>
                        <Input
                          id={`override-rule-${id}-threshold`}
                          type="number"
                          step="any"
                          inputMode="decimal"
                          aria-label={t('settings.alerts.thresholdAria', { name: ruleLabel(id) })}
                          defaultValue={spec.threshold ?? ''}
                          key={`${id}-${spec.threshold ?? ''}`}
                          onBlur={(event) => {
                            const raw = event.target.value.trim();
                            if (raw === '') {
                              event.target.value =
                                spec.threshold != null ? String(spec.threshold) : '';
                              return;
                            }
                            const parsed = Number(raw);
                            if (!Number.isFinite(parsed) || parsed === spec.threshold) {
                              event.target.value =
                                spec.threshold != null ? String(spec.threshold) : '';
                              return;
                            }
                            writeRule(id, { threshold: parsed });
                          }}
                          className="w-24"
                        />
                        {ruleUnit(id) ? (
                          <span className="text-sm text-muted-foreground">{ruleUnit(id)}</span>
                        ) : null}
                      </>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() =>
              onChange({
                ...override,
                rules: undefined,
              })
            }
          >
            {t('settings.alerts.resetToGlobal')}
          </Button>
          <Button onClick={onClose}>{t('settings.alerts.done')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
