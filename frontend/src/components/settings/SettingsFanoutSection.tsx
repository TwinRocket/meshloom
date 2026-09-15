import {
  useState,
  useEffect,
  useCallback,
  useMemo,
  useRef,
  lazy,
  Suspense,
  type ReactNode,
} from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { ChevronDown, Info } from 'lucide-react';
import i18n from '../../i18n';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Button } from '../ui/button';
import { Separator } from '../ui/separator';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../ui/dialog';
import { toast } from '../ui/sonner';
import { cn } from '@/lib/utils';
import { api } from '../../api';
import type { Channel, Contact, FanoutConfig, HealthStatus } from '../../types';

const BotCodeEditor = lazy(() =>
  import('../BotCodeEditor').then((m) => ({ default: m.BotCodeEditor }))
);

const transInline = { code: <code />, strong: <strong /> };

const FANOUT_TYPE_ORDER = [
  'mqtt_private',
  'mqtt_community',
  'mqtt_ha',
  'bot',
  'webhook',
  'apprise',
  'sqs',
  'map_upload',
] as const;

const DEFAULT_COMMUNITY_PACKET_TOPIC_TEMPLATE = 'meshcore/{IATA}/{PUBLIC_KEY}/packets';
const DEFAULT_COMMUNITY_BROKER_HOST = 'mqtt-us-v1.letsmesh.net';
const DEFAULT_COMMUNITY_BROKER_HOST_EU = 'mqtt-eu-v1.letsmesh.net';
const DEFAULT_COMMUNITY_BROKER_PORT = 443;
const DEFAULT_COMMUNITY_TRANSPORT = 'websockets';
const DEFAULT_COMMUNITY_AUTH_MODE = 'token';
const DEFAULT_MESHRANK_BROKER_HOST = 'meshrank.net';
const DEFAULT_MESHRANK_BROKER_PORT = 8883;
const DEFAULT_MESHRANK_TRANSPORT = 'tcp';
const DEFAULT_MESHRANK_AUTH_MODE = 'none';
const DEFAULT_MESHRANK_IATA = 'XYZ';

function createCommunityConfigDefaults(
  overrides: Partial<Record<string, unknown>> = {}
): Record<string, unknown> {
  return {
    broker_host: DEFAULT_COMMUNITY_BROKER_HOST,
    broker_port: DEFAULT_COMMUNITY_BROKER_PORT,
    transport: DEFAULT_COMMUNITY_TRANSPORT,
    use_tls: true,
    tls_verify: true,
    auth_mode: DEFAULT_COMMUNITY_AUTH_MODE,
    username: '',
    password: '',
    iata: '',
    email: '',
    token_audience: '',
    topic_template: DEFAULT_COMMUNITY_PACKET_TOPIC_TEMPLATE,
    ...overrides,
  };
}

const DEFAULT_BOT_CODE = `def bot(**kwargs) -> str | list[str] | None:
    """
    Process messages and optionally return a reply.

    Args:
        kwargs keys currently provided:
            sender_name: Display name of sender (may be None)
            sender_key: 64-char hex public key (None for channel msgs)
            message_text: The message content
            is_dm: True for direct messages, False for channel
            channel_key: 32-char hex key for channels, None for DMs
            channel_name: Channel name with hash (e.g. "#bot"), None for DMs
            sender_timestamp: Sender's timestamp (unix seconds, may be None)
            path: Hex-encoded routing path (may be None)
            is_outgoing: True if this is our own outgoing message
            path_bytes_per_hop: Bytes per hop in path (1, 2, or 3) when known
            scoped: True if the message carried a regional flood scope,
                False for plain/unscoped flood. Check this first. Set for
                scoped DMs too.
            region: Only meaningful when scoped is True (else always None).
                When scoped, it's the decoded region name, or None if the
                scope matched none of your known_regions. region alone can't
                distinguish unscoped from unrecognized — use scoped.

    Returns:
        None for no reply, a string for a single reply,
        a list of strings to send multiple messages in order, or a dict
        {"region": <name or None>, "message": <str or list[str]>} to scope a
        channel reply to a region for that send only (None/"" = unscoped flood;
        region is ignored for DM replies).
    """
    sender_name = kwargs.get("sender_name")
    message_text = kwargs.get("message_text", "")
    channel_name = kwargs.get("channel_name")
    is_outgoing = kwargs.get("is_outgoing", False)
    path_bytes_per_hop = kwargs.get("path_bytes_per_hop")

    # Don't reply to our own outgoing messages
    if is_outgoing:
        return None
    
    # If you want to make use of persistant data between calls to this function, 
    # you can put that data into the global _bot_globals dictionary, e.g.:
    #
    # bot_globals = globals()["_bot_globals"] 
    # if not "known_sender_names" in bot_globals:
    #     bot_globals["known_sender_names"] = set()
    #
    # bot_globals["known_sender_names"].add(sender_name)

    # Example: Only respond in #bot channel to "!pling" command
    if channel_name == "#bot" and "!pling" in message_text.lower():
        return "[BOT] Plong!"
    return None`;

type DraftType =
  | 'mqtt_private'
  | 'mqtt_ha'
  | 'mqtt_community'
  | 'mqtt_community_meshrank'
  | 'mqtt_community_letsmesh_us'
  | 'mqtt_community_letsmesh_eu'
  | 'webhook'
  | 'apprise'
  | 'sqs'
  | 'bot'
  | 'map_upload';

type CreateSectionKey = 'privateForwarding' | 'communitySharing' | 'automation';

type CreateIntegrationDefinition = {
  value: DraftType;
  savedType: string;
  sectionKey: CreateSectionKey;
  nameMode: 'counted' | 'fixed';
  defaults: {
    config: Record<string, unknown>;
    scope: Record<string, unknown>;
  };
};

function fanoutTypeLabel(type: string) {
  return i18n.t(`settings.fanout.types.${type}`, { defaultValue: type });
}

function fanoutCreateLabel(value: DraftType) {
  return i18n.t(`settings.fanout.create.${value}.label`);
}

function fanoutCreateDescription(value: DraftType) {
  return i18n.t(`settings.fanout.create.${value}.description`);
}

function fanoutCreateDefaultName(value: DraftType) {
  return i18n.t(`settings.fanout.create.${value}.defaultName`);
}

function fanoutCreateSection(sectionKey: CreateSectionKey) {
  return i18n.t(`settings.fanout.create.sections.${sectionKey}`);
}

const CREATE_INTEGRATION_DEFINITIONS: readonly CreateIntegrationDefinition[] = [
  {
    value: 'mqtt_private',
    savedType: 'mqtt_private',
    sectionKey: 'privateForwarding',
    nameMode: 'counted',
    defaults: {
      config: {
        broker_host: '',
        broker_port: 1883,
        username: '',
        password: '',
        use_tls: false,
        tls_insecure: false,
        topic_prefix: 'meshcore',
      },
      scope: { messages: 'all', raw_packets: 'all' },
    },
  },
  {
    value: 'mqtt_ha',
    savedType: 'mqtt_ha',
    sectionKey: 'privateForwarding',
    nameMode: 'fixed',
    defaults: {
      config: {
        broker_host: '',
        broker_port: 1883,
        username: '',
        password: '',
        use_tls: false,
        tls_insecure: false,
        topic_prefix: 'meshcore',
        tracked_contacts: [],
        tracked_repeaters: [],
      },
      scope: { messages: 'all', raw_packets: 'none' },
    },
  },
  {
    value: 'mqtt_community',
    savedType: 'mqtt_community',
    sectionKey: 'communitySharing',
    nameMode: 'counted',
    defaults: {
      config: createCommunityConfigDefaults(),
      scope: { messages: 'none', raw_packets: 'all' },
    },
  },
  {
    value: 'mqtt_community_meshrank',
    savedType: 'mqtt_community',
    sectionKey: 'communitySharing',
    nameMode: 'fixed',
    defaults: {
      config: createCommunityConfigDefaults({
        broker_host: DEFAULT_MESHRANK_BROKER_HOST,
        broker_port: DEFAULT_MESHRANK_BROKER_PORT,
        transport: DEFAULT_MESHRANK_TRANSPORT,
        auth_mode: DEFAULT_MESHRANK_AUTH_MODE,
        iata: DEFAULT_MESHRANK_IATA,
        email: '',
        token_audience: '',
        topic_template: '',
      }),
      scope: { messages: 'none', raw_packets: 'all' },
    },
  },
  {
    value: 'mqtt_community_letsmesh_us',
    savedType: 'mqtt_community',
    sectionKey: 'communitySharing',
    nameMode: 'fixed',
    defaults: {
      config: createCommunityConfigDefaults({
        broker_host: DEFAULT_COMMUNITY_BROKER_HOST,
        token_audience: DEFAULT_COMMUNITY_BROKER_HOST,
      }),
      scope: { messages: 'none', raw_packets: 'all' },
    },
  },
  {
    value: 'mqtt_community_letsmesh_eu',
    savedType: 'mqtt_community',
    sectionKey: 'communitySharing',
    nameMode: 'fixed',
    defaults: {
      config: createCommunityConfigDefaults({
        broker_host: DEFAULT_COMMUNITY_BROKER_HOST_EU,
        token_audience: DEFAULT_COMMUNITY_BROKER_HOST_EU,
      }),
      scope: { messages: 'none', raw_packets: 'all' },
    },
  },
  {
    value: 'webhook',
    savedType: 'webhook',
    sectionKey: 'automation',
    nameMode: 'counted',
    defaults: {
      config: {
        url: '',
        method: 'POST',
        headers: {},
        hmac_secret: '',
        hmac_header: '',
      },
      scope: { messages: 'all', raw_packets: 'none' },
    },
  },
  {
    value: 'apprise',
    savedType: 'apprise',
    sectionKey: 'automation',
    nameMode: 'counted',
    defaults: {
      config: {
        urls: '',
        preserve_identity: true,
        include_outgoing: false,
        markdown_format: true,
        body_format_dm: '**DM:** {sender_name}: {text} **via:** [{hops_backticked}]',
        body_format_channel:
          '**{channel_name}:** {sender_name}: {text} **via:** [{hops_backticked}]',
      },
      scope: { messages: 'all', raw_packets: 'none' },
    },
  },
  {
    value: 'sqs',
    savedType: 'sqs',
    sectionKey: 'privateForwarding',
    nameMode: 'counted',
    defaults: {
      config: {
        queue_url: '',
        region_name: '',
        endpoint_url: '',
        access_key_id: '',
        secret_access_key: '',
        session_token: '',
      },
      scope: { messages: 'all', raw_packets: 'none' },
    },
  },
  {
    value: 'bot',
    savedType: 'bot',
    sectionKey: 'automation',
    nameMode: 'counted',
    defaults: {
      config: {
        code: DEFAULT_BOT_CODE,
      },
      scope: { messages: 'all', raw_packets: 'none' },
    },
  },
  {
    value: 'map_upload',
    savedType: 'map_upload',
    sectionKey: 'communitySharing',
    nameMode: 'counted',
    defaults: {
      config: {
        api_url: '',
        dry_run: true,
      },
      scope: { messages: 'none', raw_packets: 'all' },
    },
  },
];

const CREATE_INTEGRATION_DEFINITIONS_BY_VALUE = Object.fromEntries(
  CREATE_INTEGRATION_DEFINITIONS.map((definition) => [definition.value, definition])
) as Record<DraftType, CreateIntegrationDefinition>;

function getNumberInputValue(value: unknown, fallback: number): string | number {
  if (value === '') return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  return fallback;
}

function getOptionalNumberInputValue(value: unknown): string | number {
  if (value === '') return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  return '';
}

function parseIntegerInputValue(value: string): number | string {
  if (value === '') return '';
  const parsed = Number.parseInt(value, 10);
  return Number.isNaN(parsed) ? value : parsed;
}

function parseFloatInputValue(value: string): number | string {
  if (value === '') return '';
  const parsed = Number.parseFloat(value);
  return Number.isNaN(parsed) ? value : parsed;
}

function normalizeIntegrationConfigForSave(
  configType: string,
  config: Record<string, unknown>
): Record<string, unknown> {
  const normalized = { ...config };

  if (configType === 'mqtt_private') {
    const port = normalized.broker_port;
    if (port === '' || port === undefined || port === null) {
      normalized.broker_port = 1883;
    } else if (typeof port === 'string') {
      const parsed = Number.parseInt(port, 10);
      normalized.broker_port = Number.isNaN(parsed) ? 1883 : parsed;
    }

    const topicPrefix = String(normalized.topic_prefix ?? '').trim();
    normalized.topic_prefix = topicPrefix || 'meshcore';
  }

  if (configType === 'mqtt_community') {
    const brokerHost = String(normalized.broker_host ?? '').trim();
    normalized.broker_host = brokerHost || DEFAULT_COMMUNITY_BROKER_HOST;

    const port = normalized.broker_port;
    if (port === '' || port === undefined || port === null) {
      normalized.broker_port = DEFAULT_COMMUNITY_BROKER_PORT;
    } else if (typeof port === 'string') {
      const parsed = Number.parseInt(port, 10);
      normalized.broker_port = Number.isNaN(parsed) ? DEFAULT_COMMUNITY_BROKER_PORT : parsed;
    }

    const topicTemplate = String(normalized.topic_template ?? '').trim();
    normalized.topic_template = topicTemplate || DEFAULT_COMMUNITY_PACKET_TOPIC_TEMPLATE;
  }

  if (configType === 'map_upload') {
    const radius = normalized.geofence_radius_km;
    if (radius === '' || radius === undefined || radius === null) {
      normalized.geofence_radius_km = 0;
    } else if (typeof radius === 'string') {
      const parsed = Number.parseFloat(radius);
      normalized.geofence_radius_km = Number.isNaN(parsed) ? 0 : parsed;
    }
  }

  return normalized;
}

function isDraftType(value: string): value is DraftType {
  return value in CREATE_INTEGRATION_DEFINITIONS_BY_VALUE;
}

function getCreateIntegrationDefinition(draftType: DraftType) {
  return CREATE_INTEGRATION_DEFINITIONS_BY_VALUE[draftType];
}

function normalizeDraftName(draftType: DraftType, name: string, configs: FanoutConfig[]) {
  const definition = getCreateIntegrationDefinition(draftType);
  if (name) return name;
  if (definition.nameMode === 'fixed') return fanoutCreateDefaultName(draftType);
  return getDefaultIntegrationName(definition.savedType, configs);
}

function normalizeDraftConfig(draftType: DraftType, config: Record<string, unknown>) {
  if (draftType === 'mqtt_community_meshrank') {
    const topicTemplate = String(config.topic_template || '').trim();
    if (!topicTemplate) {
      throw new Error(i18n.t('settings.fanout.toast.meshrankTopicRequired'));
    }

    return normalizeIntegrationConfigForSave('mqtt_community', {
      ...config,
      broker_host: DEFAULT_MESHRANK_BROKER_HOST,
      broker_port: DEFAULT_MESHRANK_BROKER_PORT,
      transport: DEFAULT_MESHRANK_TRANSPORT,
      auth_mode: DEFAULT_MESHRANK_AUTH_MODE,
      use_tls: true,
      tls_verify: true,
      iata: DEFAULT_MESHRANK_IATA,
      email: '',
      token_audience: '',
      topic_template: topicTemplate,
      username: '',
      password: '',
    });
  }

  if (draftType === 'mqtt_community_letsmesh_us' || draftType === 'mqtt_community_letsmesh_eu') {
    const brokerHost =
      draftType === 'mqtt_community_letsmesh_eu'
        ? DEFAULT_COMMUNITY_BROKER_HOST_EU
        : DEFAULT_COMMUNITY_BROKER_HOST;
    return normalizeIntegrationConfigForSave('mqtt_community', {
      ...config,
      broker_host: brokerHost,
      broker_port: DEFAULT_COMMUNITY_BROKER_PORT,
      transport: DEFAULT_COMMUNITY_TRANSPORT,
      auth_mode: DEFAULT_COMMUNITY_AUTH_MODE,
      use_tls: true,
      tls_verify: true,
      token_audience: brokerHost,
      topic_template: (config.topic_template as string) || DEFAULT_COMMUNITY_PACKET_TOPIC_TEMPLATE,
      username: '',
      password: '',
    });
  }

  return normalizeIntegrationConfigForSave(
    getCreateIntegrationDefinition(draftType).savedType,
    config
  );
}

function normalizeDraftScope(draftType: DraftType, scope: Record<string, unknown>) {
  if (getCreateIntegrationDefinition(draftType).savedType === 'mqtt_community') {
    return { messages: 'none', raw_packets: 'all' };
  }
  return scope;
}

function cloneDraftDefaults(draftType: DraftType) {
  const recipe = getCreateIntegrationDefinition(draftType);
  return {
    config: structuredClone(recipe.defaults.config),
    scope: structuredClone(recipe.defaults.scope),
  };
}

function CreateIntegrationDialog({
  open,
  options,
  selectedType,
  onOpenChange,
  onSelect,
  onCreate,
}: {
  open: boolean;
  options: readonly CreateIntegrationDefinition[];
  selectedType: DraftType | null;
  onOpenChange: (open: boolean) => void;
  onSelect: (type: DraftType) => void;
  onCreate: () => void;
}) {
  const { t } = useTranslation();
  const selectedOption =
    options.find((option) => option.value === selectedType) ?? options[0] ?? null;
  const listRef = useRef<HTMLDivElement | null>(null);
  const [showScrollHint, setShowScrollHint] = useState(false);

  const updateScrollHint = useCallback(() => {
    const container = listRef.current;
    if (!container) {
      setShowScrollHint(false);
      return;
    }
    setShowScrollHint(container.scrollTop + container.clientHeight < container.scrollHeight - 8);
  }, []);

  useEffect(() => {
    if (!open) return;
    const frame = window.requestAnimationFrame(updateScrollHint);
    window.addEventListener('resize', updateScrollHint);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('resize', updateScrollHint);
    };
  }, [open, options, updateScrollHint]);

  const sectionedOptions = [...new Set(options.map((o) => o.sectionKey))]
    .map((sectionKey) => ({
      sectionKey,
      section: fanoutCreateSection(sectionKey),
      options: options.filter((option) => option.sectionKey === sectionKey),
    }))
    .filter((group) => group.options.length > 0);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        aria-describedby={undefined}
        hideCloseButton
        className="flex dialog-safe-height w-[96vw] max-w-[960px] flex-col overflow-hidden p-0 sm:rounded-xl"
      >
        <DialogHeader className="border-b border-border px-5 py-4">
          <DialogTitle>{t('settings.fanout.create.title')}</DialogTitle>
        </DialogHeader>

        <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden md:grid-cols-[240px_minmax(0,1fr)]">
          <div className="relative border-b border-border bg-muted/20 md:border-b-0 md:border-r">
            <div
              ref={listRef}
              onScroll={updateScrollHint}
              className="max-h-56 overflow-y-auto p-2 md:max-h-[420px]"
            >
              <div className="space-y-4">
                {sectionedOptions.map((group) => (
                  <div key={group.sectionKey} className="space-y-1.5">
                    <div className="px-2 text-[0.6875rem] font-semibold uppercase tracking-wider text-muted-foreground">
                      {group.section}
                    </div>
                    {group.options.map((option) => {
                      const selected = option.value === selectedOption?.value;
                      return (
                        <button
                          key={option.value}
                          type="button"
                          className={cn(
                            'w-full rounded-md border px-3 py-2 text-left transition-colors',
                            selected
                              ? 'border-primary bg-accent text-foreground'
                              : 'border-transparent bg-transparent hover:bg-accent/70'
                          )}
                          aria-pressed={selected}
                          onClick={() => onSelect(option.value)}
                        >
                          <div className="text-sm font-medium">
                            {fanoutCreateLabel(option.value)}
                          </div>
                        </button>
                      );
                    })}
                  </div>
                ))}
              </div>
            </div>

            {showScrollHint && (
              <div className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-center bg-gradient-to-t from-background via-background/85 to-transparent px-4 pb-2 pt-8">
                <div className="rounded-full border border-border/80 bg-background/95 px-2 py-1 text-muted-foreground shadow-sm">
                  <ChevronDown className="h-4 w-4" aria-hidden="true" />
                </div>
              </div>
            )}
          </div>

          <div className="min-h-0 space-y-4 overflow-y-auto px-5 py-5 md:min-h-[280px] md:max-h-[420px]">
            {selectedOption ? (
              <>
                <div className="space-y-1.5">
                  <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    {fanoutCreateSection(selectedOption.sectionKey)}
                  </div>
                  <h3 className="text-lg font-semibold">
                    {fanoutCreateLabel(selectedOption.value)}
                  </h3>
                </div>

                <p className="text-sm leading-6 text-muted-foreground">
                  {fanoutCreateDescription(selectedOption.value)}
                </p>
              </>
            ) : (
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                {t('settings.fanout.create.empty')}
              </div>
            )}
          </div>
        </div>

        <DialogFooter className="gap-2 border-t border-border px-5 py-4 sm:justify-end">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('settings.fanout.create.close')}
          </Button>
          <Button onClick={onCreate} disabled={!selectedOption}>
            {t('settings.fanout.create.create')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function getDetailTypeLabel(detailType: string) {
  if (isDraftType(detailType)) return fanoutCreateLabel(detailType);
  return fanoutTypeLabel(detailType);
}

function fanoutDraftHasUnsavedChanges(
  original: FanoutConfig | null,
  current: {
    name: string;
    config: Record<string, unknown>;
    scope: Record<string, unknown>;
  }
) {
  if (!original) return false;
  return (
    current.name !== original.name ||
    JSON.stringify(current.config) !== JSON.stringify(original.config) ||
    JSON.stringify(current.scope) !== JSON.stringify(original.scope)
  );
}

function formatBrokerSummary(
  config: Record<string, unknown>,
  defaults: { host: string; port: number }
) {
  const host = (config.broker_host as string) || defaults.host;
  const port = typeof config.broker_port === 'number' ? config.broker_port : defaults.port;
  return `${host}:${port}`;
}

function formatPrivateTopicSummary(config: Record<string, unknown>) {
  const prefix = (config.topic_prefix as string) || 'meshcore';
  return `${prefix}/dm:<pubkey>, ${prefix}/gm:<channel>, ${prefix}/raw/...`;
}

function censorAppriseUrl(url: string): string {
  const protoMatch = url.match(/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//);
  if (protoMatch) return `${protoMatch[0]}********`;
  return '********';
}

function formatAppriseTargets(urls: string | undefined) {
  const targets = (urls || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
  if (targets.length === 0) return i18n.t('settings.fanout.apprise.noTargets');

  return targets.map(censorAppriseUrl).join(', ');
}

function formatSqsQueueSummary(config: Record<string, unknown>) {
  const queueUrl = ((config.queue_url as string) || '').trim();
  if (!queueUrl) return i18n.t('settings.fanout.sqs.noQueue');
  return queueUrl;
}

function getDefaultIntegrationName(type: string, configs: FanoutConfig[]) {
  const label = fanoutTypeLabel(type);
  const nextIndex = configs.filter((cfg) => cfg.type === type).length + 1;
  return i18n.t('settings.fanout.defaultNameCounted', { label, n: nextIndex });
}

function getStatusLabel(status: string | undefined, type?: string) {
  if (status === 'connected')
    return type === 'bot' || type === 'webhook' || type === 'apprise' || type === 'map_upload'
      ? i18n.t('settings.fanout.status.active')
      : i18n.t('settings.fanout.status.connected');
  if (status === 'error') return i18n.t('settings.fanout.status.error');
  if (status === 'disconnected') return i18n.t('settings.fanout.status.disconnected');
  return i18n.t('settings.fanout.status.inactive');
}

function getStatusColor(status: string | undefined, enabled?: boolean) {
  if (enabled === false) return 'bg-muted-foreground';
  if (status === 'connected')
    return 'bg-status-connected shadow-[0_0_6px_hsl(var(--status-connected)/0.5)]';
  if (status === 'error' || status === 'disconnected')
    return 'bg-destructive shadow-[0_0_6px_hsl(var(--destructive)/0.5)]';
  return 'bg-muted-foreground';
}

function MqttPrivateConfigEditor({
  config,
  scope,
  onChange,
  onScopeChange,
}: {
  config: Record<string, unknown>;
  scope: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
  onScopeChange: (scope: Record<string, unknown>) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="space-y-3">
      <p className="text-[0.8125rem] text-muted-foreground">
        {t('settings.fanout.mqtt.privateHelp')}
      </p>

      <div className="rounded-md border border-warning/50 bg-warning/10 px-3 py-2 text-xs text-warning">
        {t('settings.fanout.mqtt.outgoingWarning')}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-mqtt-host">{t('settings.fanout.mqtt.brokerHost')}</Label>
          <Input
            id="fanout-mqtt-host"
            type="text"
            placeholder={t('settings.fanout.mqtt.hostPlaceholder')}
            value={(config.broker_host as string) || ''}
            onChange={(e) => onChange({ ...config, broker_host: e.target.value })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="fanout-mqtt-port">{t('settings.fanout.mqtt.brokerPort')}</Label>
          <Input
            id="fanout-mqtt-port"
            type="number"
            min="1"
            max="65535"
            value={getNumberInputValue(config.broker_port, 1883)}
            onChange={(e) =>
              onChange({ ...config, broker_port: parseIntegerInputValue(e.target.value) })
            }
          />
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-mqtt-user">{t('settings.fanout.mqtt.username')}</Label>
          <Input
            id="fanout-mqtt-user"
            type="text"
            placeholder={t('settings.fanout.mqtt.optional')}
            value={(config.username as string) || ''}
            onChange={(e) => onChange({ ...config, username: e.target.value })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="fanout-mqtt-pass">{t('settings.fanout.mqtt.password')}</Label>
          <Input
            id="fanout-mqtt-pass"
            type="password"
            placeholder={t('settings.fanout.mqtt.optional')}
            value={(config.password as string) || ''}
            onChange={(e) => onChange({ ...config, password: e.target.value })}
          />
        </div>
      </div>

      <label className="flex items-center gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={!!config.use_tls}
          onChange={(e) => onChange({ ...config, use_tls: e.target.checked })}
          className="h-4 w-4 rounded border-border"
        />
        <span className="text-sm">{t('settings.fanout.mqtt.useTls')}</span>
      </label>

      {!!config.use_tls && (
        <label className="flex items-center gap-3 cursor-pointer ml-7">
          <input
            type="checkbox"
            checked={!!config.tls_insecure}
            onChange={(e) => onChange({ ...config, tls_insecure: e.target.checked })}
            className="h-4 w-4 rounded border-border"
          />
          <span className="text-sm">{t('settings.fanout.mqtt.skipCert')}</span>
        </label>
      )}

      <Separator />

      <div className="space-y-2">
        <Label htmlFor="fanout-mqtt-prefix">{t('settings.fanout.mqtt.topicPrefix')}</Label>
        <Input
          id="fanout-mqtt-prefix"
          type="text"
          placeholder="meshcore"
          value={(config.topic_prefix as string | undefined) ?? ''}
          onChange={(e) => onChange({ ...config, topic_prefix: e.target.value })}
        />
      </div>

      <Separator />

      <ScopeSelector scope={scope} onChange={onScopeChange} showRawPackets />
    </div>
  );
}

function MqttHaConfigEditor({
  config,
  scope,
  onChange,
  onScopeChange,
}: {
  config: Record<string, unknown>;
  scope: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
  onScopeChange: (scope: Record<string, unknown>) => void;
}) {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [trackedRepeaters, setTrackedRepeaters] = useState<string[]>([]);
  const [contactSearch, setContactSearch] = useState('');
  const [radioConfig, setRadioConfig] = useState<{ public_key: string; name: string } | null>(null);
  const { t } = useTranslation();

  useEffect(() => {
    (async () => {
      const all: Contact[] = [];
      const pageSize = 1000;
      let offset = 0;
      while (true) {
        const page = await api.getContacts(pageSize, offset);
        all.push(...page);
        if (page.length < pageSize) break;
        offset += pageSize;
      }
      setContacts(all);
    })().catch(console.error);

    api
      .getRadioConfig()
      .then((radio) => setRadioConfig({ public_key: radio.public_key, name: radio.name }))
      .catch(console.error);

    api
      .getSettings()
      .then((s) => setTrackedRepeaters(s.tracked_telemetry_repeaters ?? []))
      .catch(console.error);
  }, []);

  const selectedContacts = (config.tracked_contacts as string[]) || [];
  const selectedRepeaters = (config.tracked_repeaters as string[]) || [];

  const contactOptions = useMemo(
    () => contacts.filter((c) => c.type === 0 || c.type === 1 || c.type === 3),
    [contacts]
  );

  const repeaterOptions = useMemo(
    () => contacts.filter((c) => c.type === 2 && trackedRepeaters.includes(c.public_key)),
    [contacts, trackedRepeaters]
  );

  const contactSearchLower = contactSearch.toLowerCase().trim();
  const filteredContacts = useMemo(() => {
    const matches = contactOptions.filter((c) => {
      if (!contactSearchLower) return true;
      const name = (c.name || '').toLowerCase();
      const key = c.public_key.toLowerCase();
      return name.includes(contactSearchLower) || key.startsWith(contactSearchLower);
    });
    // Selected contacts sort to top
    return matches.sort((a, b) => {
      const aSelected = selectedContacts.includes(a.public_key) ? 0 : 1;
      const bSelected = selectedContacts.includes(b.public_key) ? 0 : 1;
      if (aSelected !== bSelected) return aSelected - bSelected;
      return (a.name || a.public_key).localeCompare(b.name || b.public_key);
    });
  }, [contactOptions, contactSearchLower, selectedContacts]);

  const selectedContactDetails = contactOptions.filter((c) =>
    selectedContacts.includes(c.public_key)
  );
  const selectedRepeaterDetails = repeaterOptions.filter((c) =>
    selectedRepeaters.includes(c.public_key)
  );
  const prefix = ((config.topic_prefix as string) || 'meshcore').trim() || 'meshcore';

  const nodeIdForKey = useCallback((publicKey: string) => publicKey.slice(0, 12).toLowerCase(), []);

  const topicSummary = useMemo(() => {
    const items: Array<{
      kind: 'radio' | 'event' | 'repeater' | 'contact';
      label: string;
      publicKey: string;
      nodeId: string;
      topics: string[];
    }> = [];

    if (radioConfig?.public_key) {
      const nodeId = nodeIdForKey(radioConfig.public_key);
      items.push({
        kind: 'radio',
        label: radioConfig.name || radioConfig.public_key.slice(0, 12),
        publicKey: radioConfig.public_key,
        nodeId,
        topics: [`${prefix}/${nodeId}/health`],
      });
      items.push({
        kind: 'event',
        label: radioConfig.name || radioConfig.public_key.slice(0, 12),
        publicKey: radioConfig.public_key,
        nodeId,
        topics: [`${prefix}/${nodeId}/events/message`],
      });
    }

    for (const repeater of selectedRepeaterDetails) {
      const nodeId = nodeIdForKey(repeater.public_key);
      items.push({
        kind: 'repeater',
        label: repeater.name || repeater.public_key.slice(0, 12),
        publicKey: repeater.public_key,
        nodeId,
        topics: [`${prefix}/${nodeId}/telemetry`],
      });
    }

    for (const contact of selectedContactDetails) {
      const nodeId = nodeIdForKey(contact.public_key);
      items.push({
        kind: 'contact',
        label: contact.name || contact.public_key.slice(0, 12),
        publicKey: contact.public_key,
        nodeId,
        topics: [`${prefix}/${nodeId}/gps`],
      });
    }

    return items;
  }, [nodeIdForKey, prefix, radioConfig, selectedContactDetails, selectedRepeaterDetails]);

  const kindLabel: Record<(typeof topicSummary)[number]['kind'], string> = {
    radio: t('settings.fanout.ha.kindRadio'),
    event: t('settings.fanout.ha.kindEvent'),
    repeater: t('settings.fanout.ha.kindRepeater'),
    contact: t('settings.fanout.ha.kindContact'),
  };
  const localRadioNodeId = radioConfig?.public_key
    ? nodeIdForKey(radioConfig.public_key)
    : '<radio_node_id>';
  const exampleRepeaterNodeId =
    selectedRepeaterDetails.length > 0
      ? nodeIdForKey(selectedRepeaterDetails[0].public_key)
      : '<repeater_node_id>';
  const exampleContactNodeId =
    selectedContactDetails.length > 0
      ? nodeIdForKey(selectedContactDetails[0].public_key)
      : '<contact_node_id>';

  const toggleTrackedContact = (key: string) => {
    const current = [...selectedContacts];
    const idx = current.indexOf(key);
    if (idx >= 0) current.splice(idx, 1);
    else current.push(key);
    onChange({ ...config, tracked_contacts: current });
  };

  const toggleTrackedRepeater = (key: string) => {
    const current = [...selectedRepeaters];
    const idx = current.indexOf(key);
    if (idx >= 0) current.splice(idx, 1);
    else current.push(key);
    onChange({ ...config, tracked_repeaters: current });
  };

  return (
    <div className="space-y-3">
      <div className="space-y-3 rounded-lg border border-primary/20 bg-primary/5 p-4">
        <div className="space-y-1">
          <h3 className="text-base font-semibold tracking-tight">
            {t('settings.fanout.ha.title')}
          </h3>
          <p className="text-sm text-muted-foreground">{t('settings.fanout.ha.help')}</p>
        </div>

        <div className="grid gap-2 md:grid-cols-3">
          <div className="rounded-md border border-border/70 bg-background/80 p-3">
            <div className="text-sm font-medium text-foreground">
              {t('settings.fanout.ha.step1Title')}
            </div>
            <p className="mt-1 text-[0.8125rem] text-muted-foreground">
              {t('settings.fanout.ha.step1Help')}
            </p>
          </div>
          <div className="rounded-md border border-border/70 bg-background/80 p-3">
            <div className="text-sm font-medium text-foreground">
              {t('settings.fanout.ha.step2Title')}
            </div>
            <p className="mt-1 text-[0.8125rem] text-muted-foreground">
              {t('settings.fanout.ha.step2Help')}
            </p>
          </div>
          <div className="rounded-md border border-border/70 bg-background/80 p-3">
            <div className="text-sm font-medium text-foreground">
              {t('settings.fanout.ha.step3Title')}
            </div>
            <p className="mt-1 text-[0.8125rem] text-muted-foreground">
              {t('settings.fanout.ha.step3Help')}
            </p>
          </div>
        </div>

        <p className="text-[0.8125rem] text-muted-foreground">
          <Trans
            i18nKey="settings.fanout.ha.usesDiscovery"
            components={{
              ...transInline,
              discovery: (
                <span
                  role="link"
                  tabIndex={0}
                  className="underline cursor-pointer hover:text-primary transition-colors"
                  onClick={() =>
                    window.open(
                      'https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery',
                      '_blank'
                    )
                  }
                  onKeyDown={(e) => {
                    if (e.key === 'Enter')
                      window.open(
                        'https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery',
                        '_blank'
                      );
                  }}
                />
              ),
              readme: (
                <span
                  role="link"
                  tabIndex={0}
                  className="underline cursor-pointer hover:text-primary transition-colors"
                  onClick={() =>
                    window.open(
                      'https://github.com/bagl3y/meshloom/blob/main/README_HA.md',
                      '_blank'
                    )
                  }
                  onKeyDown={(e) => {
                    if (e.key === 'Enter')
                      window.open(
                        'https://github.com/bagl3y/meshloom/blob/main/README_HA.md',
                        '_blank'
                      );
                  }}
                />
              ),
            }}
          />
        </p>
      </div>

      <details className="group">
        <summary className="text-sm font-medium text-foreground cursor-pointer select-none flex items-center gap-1">
          <ChevronDown className="h-3 w-3 transition-transform group-open:rotate-0 -rotate-90" />
          {t('settings.fanout.ha.whatGetsCreated')}
        </summary>
        <div className="mt-2 space-y-2 text-sm text-muted-foreground rounded-md border border-border bg-muted/20 p-3">
          <div>
            <span className="font-medium text-foreground">
              {t('settings.fanout.ha.localRadio')}
            </span>{' '}
            {t('settings.fanout.ha.localRadioAlways')}
            <span className="ml-1">{t('settings.fanout.ha.updatesEvery60s')}</span>
            <ul className="mt-0.5 ml-4 list-disc space-y-0.5">
              <li>
                <code className="text-[0.6875rem]">
                  {`binary_sensor.meshcore_${localRadioNodeId}_connected`}
                </code>{' '}
                {t('settings.fanout.ha.radioOnline')}
              </li>
              <li>
                <code className="text-[0.6875rem]">
                  {`sensor.meshcore_${localRadioNodeId}_noise_floor`}
                </code>{' '}
                {t('settings.fanout.ha.radioNoiseFloor')}
              </li>
            </ul>
          </div>

          <div>
            <span className="font-medium text-foreground">
              {t('settings.fanout.ha.perRepeater')}
            </span>{' '}
            {t('settings.fanout.ha.perRepeaterHelp')}
            <ul className="mt-0.5 ml-4 list-disc space-y-0.5">
              <li>
                <code className="text-[0.6875rem]">
                  {`sensor.meshcore_${exampleRepeaterNodeId}_battery_voltage`}
                </code>{' '}
                (V)
              </li>
              <li>
                <code className="text-[0.6875rem]">
                  {`sensor.meshcore_${exampleRepeaterNodeId}_noise_floor`}
                </code>
                ,{' '}
                <code className="text-[0.6875rem]">
                  {`sensor.meshcore_${exampleRepeaterNodeId}_last_rssi`}
                </code>
                ,{' '}
                <code className="text-[0.6875rem]">
                  {`sensor.meshcore_${exampleRepeaterNodeId}_last_snr`}
                </code>{' '}
                (dBm/dB)
              </li>
              <li>
                <code className="text-[0.6875rem]">
                  {`sensor.meshcore_${exampleRepeaterNodeId}_packets_received`}
                </code>
                ,{' '}
                <code className="text-[0.6875rem]">
                  {`sensor.meshcore_${exampleRepeaterNodeId}_packets_sent`}
                </code>
              </li>
              <li>
                <code className="text-[0.6875rem]">
                  {`sensor.meshcore_${exampleRepeaterNodeId}_uptime`}
                </code>{' '}
                (seconds)
              </li>
              <li>
                <code className="text-[0.6875rem]">
                  {`sensor.meshcore_${exampleRepeaterNodeId}_lpp_temperature_ch1`}
                </code>
                ,{' '}
                <code className="text-[0.6875rem]">
                  {`sensor.meshcore_${exampleRepeaterNodeId}_lpp_humidity_ch1`}
                </code>
                {t('settings.fanout.ha.lppAuto')}
              </li>
            </ul>
          </div>

          <div>
            <span className="font-medium text-foreground">
              {t('settings.fanout.ha.perContact')}
            </span>{' '}
            {t('settings.fanout.ha.perContactHelp')}
            <ul className="mt-0.5 ml-4 list-disc space-y-0.5">
              <li>
                <code className="text-[0.6875rem]">
                  {`device_tracker.meshcore_${exampleContactNodeId}`}
                </code>{' '}
                {t('settings.fanout.ha.trackerHelp')}
              </li>
              <li>
                <code className="text-[0.6875rem]">
                  {`sensor.meshcore_${exampleContactNodeId}_lpp_temperature_ch1`}
                </code>
                {t('settings.fanout.ha.contactLpp')}
              </li>
            </ul>
          </div>

          <div>
            <span className="font-medium text-foreground">
              {t('settings.fanout.ha.messageEventsTitle')}
            </span>{' '}
            {t('settings.fanout.ha.messageEventsFires')}
            <ul className="mt-0.5 ml-4 list-disc space-y-0.5">
              <li>
                <code className="text-[0.6875rem]">
                  {`event.meshcore_${localRadioNodeId}_messages`}
                </code>{' '}
                {t('settings.fanout.ha.messageEventsTrigger')}
              </li>
            </ul>
          </div>

          <p className="text-[0.6875rem] mt-1.5">
            <Trans
              i18nKey="settings.fanout.ha.entityIdsHelp"
              values={{ prefix }}
              components={transInline}
            />
          </p>
        </div>
      </details>

      <details className="group">
        <summary className="text-sm font-medium text-foreground cursor-pointer select-none flex items-center gap-1">
          <ChevronDown className="h-3 w-3 transition-transform group-open:rotate-0 -rotate-90" />
          {t('settings.fanout.ha.publishedTopicSummary')}
        </summary>
        <div className="mt-2 space-y-2 rounded-md border border-border bg-muted/20 p-3">
          <p className="text-xs text-muted-foreground">
            {t('settings.fanout.ha.topicSummaryHelp')}
          </p>
          {topicSummary.length === 0 ? (
            <p className="text-xs text-muted-foreground italic">
              {t('settings.fanout.ha.noTopicPreviews')}
            </p>
          ) : (
            <div className="space-y-2">
              {topicSummary.map((item) => (
                <div
                  key={`${item.kind}-${item.publicKey}`}
                  className="rounded border border-border/70 bg-background/70 p-2"
                >
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                    <span className="font-medium text-foreground">{kindLabel[item.kind]}</span>
                    <span className="text-foreground">{item.label}</span>
                    <span className="font-mono text-[0.6875rem] text-muted-foreground">
                      {t('settings.fanout.ha.nodeId', { id: item.nodeId })}
                    </span>
                  </div>
                  <div className="mt-1 text-[0.6875rem] text-muted-foreground font-mono break-all">
                    {t('settings.fanout.ha.key', { key: item.publicKey })}
                  </div>
                  {item.topics.map((topic) => (
                    <div
                      key={topic}
                      className="mt-1 rounded bg-muted px-2 py-1 text-[0.6875rem] font-mono text-foreground break-all"
                    >
                      {topic}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )}
          <p className="text-[0.6875rem] text-muted-foreground">
            <Trans i18nKey="settings.fanout.ha.discoveryTopics" components={transInline} />
          </p>
        </div>
      </details>

      <Separator />

      <h3 className="text-base font-semibold tracking-tight">
        {t('settings.fanout.ha.mqttBroker')}
      </h3>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-ha-host">{t('settings.fanout.mqtt.brokerHost')}</Label>
          <Input
            id="fanout-ha-host"
            type="text"
            placeholder={t('settings.fanout.mqtt.hostPlaceholder')}
            value={(config.broker_host as string) || ''}
            onChange={(e) => onChange({ ...config, broker_host: e.target.value })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="fanout-ha-port">{t('settings.fanout.mqtt.brokerPort')}</Label>
          <Input
            id="fanout-ha-port"
            type="number"
            min="1"
            max="65535"
            value={getNumberInputValue(config.broker_port, 1883)}
            onChange={(e) =>
              onChange({ ...config, broker_port: parseIntegerInputValue(e.target.value) })
            }
          />
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-ha-user">{t('settings.fanout.mqtt.username')}</Label>
          <Input
            id="fanout-ha-user"
            type="text"
            placeholder={t('settings.fanout.mqtt.optional')}
            value={(config.username as string) || ''}
            onChange={(e) => onChange({ ...config, username: e.target.value })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="fanout-ha-pass">{t('settings.fanout.mqtt.password')}</Label>
          <Input
            id="fanout-ha-pass"
            type="password"
            placeholder={t('settings.fanout.mqtt.optional')}
            value={(config.password as string) || ''}
            onChange={(e) => onChange({ ...config, password: e.target.value })}
          />
        </div>
      </div>

      <label className="flex items-center gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={!!config.use_tls}
          onChange={(e) => onChange({ ...config, use_tls: e.target.checked })}
          className="h-4 w-4 rounded border-border"
        />
        <span className="text-sm">{t('settings.fanout.mqtt.useTls')}</span>
      </label>

      {!!config.use_tls && (
        <label className="flex items-center gap-3 cursor-pointer ml-7">
          <input
            type="checkbox"
            checked={!!config.tls_insecure}
            onChange={(e) => onChange({ ...config, tls_insecure: e.target.checked })}
            className="h-4 w-4 rounded border-border"
          />
          <span className="text-sm">{t('settings.fanout.mqtt.skipCert')}</span>
        </label>
      )}

      <div className="space-y-2">
        <Label htmlFor="fanout-ha-prefix">{t('settings.fanout.mqtt.topicPrefix')}</Label>
        <Input
          id="fanout-ha-prefix"
          type="text"
          placeholder="meshcore"
          value={(config.topic_prefix as string | undefined) ?? ''}
          onChange={(e) => onChange({ ...config, topic_prefix: e.target.value })}
        />
        <p className="text-[0.6875rem] text-muted-foreground">
          <Trans
            i18nKey="settings.fanout.ha.topicPrefixHelp"
            values={{ prefix }}
            components={transInline}
          />
        </p>
      </div>

      <Separator />

      <div className="space-y-2">
        <h3 className="text-base font-semibold tracking-tight">
          {t('settings.fanout.ha.gpsTitle')}
        </h3>
        <p className="text-[0.8125rem] text-muted-foreground">
          <Trans i18nKey="settings.fanout.ha.gpsHelp" components={transInline} />
        </p>

        {selectedContactDetails.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {selectedContactDetails.map((c) => (
              <span
                key={c.public_key}
                className="inline-flex items-center gap-1 text-[0.6875rem] px-2 py-0.5 rounded-full bg-primary/10 text-primary"
              >
                {c.name || c.public_key.slice(0, 12)}
                <button
                  type="button"
                  className="ml-0.5 hover:text-destructive transition-colors"
                  onClick={() => toggleTrackedContact(c.public_key)}
                  aria-label={t('settings.fanout.ha.removeAria', {
                    name: c.name || c.public_key.slice(0, 12),
                  })}
                >
                  &times;
                </button>
              </span>
            ))}
          </div>
        )}

        {contactOptions.length === 0 ? (
          <p className="text-[0.8125rem] text-muted-foreground italic">
            {t('settings.fanout.ha.noContacts')}
          </p>
        ) : (
          <>
            <Input
              type="text"
              placeholder={t('settings.fanout.ha.searchContacts', { count: contactOptions.length })}
              value={contactSearch}
              onChange={(e) => setContactSearch(e.target.value)}
              className="h-8 text-sm"
            />
            <div className="max-h-48 overflow-y-auto space-y-1 rounded border border-border p-2">
              {filteredContacts.length === 0 ? (
                <p className="text-[0.8125rem] text-muted-foreground italic py-1">
                  {t('settings.fanout.ha.noContactsMatch', { query: contactSearch })}
                </p>
              ) : (
                filteredContacts.map((c) => (
                  <label
                    key={c.public_key}
                    className="flex items-center gap-2 cursor-pointer text-sm"
                  >
                    <input
                      type="checkbox"
                      checked={selectedContacts.includes(c.public_key)}
                      onChange={() => toggleTrackedContact(c.public_key)}
                      className="h-3.5 w-3.5 rounded border-border"
                    />
                    <span className="truncate">{c.name || c.public_key.slice(0, 12)}</span>
                    <span className="text-[0.625rem] text-muted-foreground ml-auto font-mono shrink-0">
                      {c.public_key.slice(0, 12)}
                    </span>
                  </label>
                ))
              )}
            </div>
          </>
        )}
      </div>

      <Separator />

      <div className="space-y-2">
        <h3 className="text-base font-semibold tracking-tight">
          {t('settings.fanout.ha.telemetryTitle')}
        </h3>
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.fanout.ha.telemetryHelp')}
        </p>
        {trackedRepeaters.length === 0 ? (
          <div className="rounded-md border border-muted bg-muted/30 px-3 py-2 text-[0.8125rem] text-muted-foreground">
            {t('settings.fanout.ha.noTrackedRepeaters')}
          </div>
        ) : repeaterOptions.length === 0 ? (
          <p className="text-[0.8125rem] text-muted-foreground italic">
            {t('settings.fanout.ha.trackedNotFound')}
          </p>
        ) : (
          <div className="max-h-40 overflow-y-auto space-y-1 rounded border border-border p-2">
            {repeaterOptions.map((c) => (
              <label key={c.public_key} className="flex items-center gap-2 cursor-pointer text-sm">
                <input
                  type="checkbox"
                  checked={selectedRepeaters.includes(c.public_key)}
                  onChange={() => toggleTrackedRepeater(c.public_key)}
                  className="h-3.5 w-3.5 rounded border-border"
                />
                <span className="truncate">{c.name || c.public_key.slice(0, 12)}</span>
                <span className="text-[0.625rem] text-muted-foreground ml-auto font-mono">
                  {c.public_key.slice(0, 12)}
                </span>
              </label>
            ))}
          </div>
        )}
      </div>

      <Separator />

      <div className="space-y-2">
        <h3 className="text-base font-semibold tracking-tight">
          {t('settings.fanout.ha.messageEventsHeading')}
        </h3>
        <p className="text-[0.8125rem] text-muted-foreground">
          <Trans
            i18nKey="settings.fanout.ha.messageEventsHelp"
            values={{ entity: `event.meshcore_${localRadioNodeId}_messages` }}
            components={transInline}
          />
        </p>
      </div>
      <ScopeSelector scope={scope} onChange={onScopeChange} />
    </div>
  );
}

function MqttCommunityConfigEditor({
  config,
  onChange,
}: {
  config: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
}) {
  const authMode = (config.auth_mode as string) || DEFAULT_COMMUNITY_AUTH_MODE;
  const { t } = useTranslation();

  return (
    <div className="space-y-3">
      <p className="text-[0.8125rem] text-muted-foreground">
        {t('settings.fanout.community.help')}
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-comm-host">{t('settings.fanout.mqtt.brokerHost')}</Label>
          <Input
            id="fanout-comm-host"
            type="text"
            placeholder={DEFAULT_COMMUNITY_BROKER_HOST}
            value={(config.broker_host as string | undefined) ?? ''}
            onChange={(e) => onChange({ ...config, broker_host: e.target.value })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="fanout-comm-port">{t('settings.fanout.mqtt.brokerPort')}</Label>
          <Input
            id="fanout-comm-port"
            type="number"
            min="1"
            max="65535"
            value={getNumberInputValue(config.broker_port, DEFAULT_COMMUNITY_BROKER_PORT)}
            onChange={(e) =>
              onChange({
                ...config,
                broker_port: parseIntegerInputValue(e.target.value),
              })
            }
          />
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-comm-transport">{t('settings.fanout.community.transport')}</Label>
          <select
            id="fanout-comm-transport"
            value={(config.transport as string) || DEFAULT_COMMUNITY_TRANSPORT}
            onChange={(e) => onChange({ ...config, transport: e.target.value })}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          >
            <option value="websockets">{t('settings.fanout.community.websockets')}</option>
            <option value="tcp">{t('settings.fanout.community.tcp')}</option>
          </select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="fanout-comm-auth-mode">
            {t('settings.fanout.community.authentication')}
          </Label>
          <select
            id="fanout-comm-auth-mode"
            value={authMode}
            onChange={(e) => onChange({ ...config, auth_mode: e.target.value })}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          >
            <option value="token">{t('settings.fanout.community.token')}</option>
            <option value="none">{t('settings.fanout.community.none')}</option>
            <option value="password">{t('settings.fanout.community.passwordAuth')}</option>
          </select>
          <p className="text-[0.8125rem] text-muted-foreground">
            <Trans i18nKey="settings.fanout.community.letsMeshAuthHelp" components={transInline} />
          </p>
        </div>
      </div>

      {((config.transport as string) || DEFAULT_COMMUNITY_TRANSPORT) === 'websockets' && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="fanout-comm-ws-path">{t('settings.fanout.community.wsPath')}</Label>
            <Input
              id="fanout-comm-ws-path"
              type="text"
              placeholder="/"
              value={(config.websocket_path as string | undefined) ?? ''}
              onChange={(e) => onChange({ ...config, websocket_path: e.target.value })}
            />
            <p className="text-[0.8125rem] text-muted-foreground">
              <Trans i18nKey="settings.fanout.community.wsPathHelp" components={transInline} />
            </p>
          </div>
        </div>
      )}

      {authMode === 'token' && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="fanout-comm-token-audience">
              {t('settings.fanout.community.tokenAudience')}
            </Label>
            <Input
              id="fanout-comm-token-audience"
              type="text"
              placeholder={(config.broker_host as string) || DEFAULT_COMMUNITY_BROKER_HOST}
              value={(config.token_audience as string | undefined) ?? ''}
              onChange={(e) => onChange({ ...config, token_audience: e.target.value })}
            />
            <p className="text-[0.8125rem] text-muted-foreground">
              {t('settings.fanout.community.tokenAudienceHelp')}
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="fanout-comm-email">{t('settings.fanout.community.ownerEmail')}</Label>
            <Input
              id="fanout-comm-email"
              type="email"
              placeholder={t('settings.fanout.community.emailPlaceholder')}
              value={(config.email as string) || ''}
              onChange={(e) => onChange({ ...config, email: e.target.value })}
            />
            <p className="text-[0.8125rem] text-muted-foreground">
              {t('settings.fanout.community.ownerEmailHelp')}
            </p>
          </div>
        </div>
      )}

      {authMode === 'password' && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="fanout-comm-username">{t('settings.fanout.mqtt.username')}</Label>
            <Input
              id="fanout-comm-username"
              type="text"
              value={(config.username as string) || ''}
              onChange={(e) => onChange({ ...config, username: e.target.value })}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="fanout-comm-password">{t('settings.fanout.mqtt.password')}</Label>
            <Input
              id="fanout-comm-password"
              type="password"
              value={(config.password as string) || ''}
              onChange={(e) => onChange({ ...config, password: e.target.value })}
            />
          </div>
        </div>
      )}

      <div className="space-y-2">
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={config.use_tls === undefined ? true : !!config.use_tls}
            onChange={(e) => onChange({ ...config, use_tls: e.target.checked })}
            className="h-4 w-4 rounded border-border"
          />
          <span className="text-sm">{t('settings.fanout.mqtt.useTls')}</span>
        </label>

        <label className="flex items-center gap-3 cursor-pointer ml-7">
          <input
            type="checkbox"
            checked={config.tls_verify === undefined ? true : !!config.tls_verify}
            onChange={(e) => onChange({ ...config, tls_verify: e.target.checked })}
            className="h-4 w-4 rounded border-border"
            disabled={config.use_tls === undefined ? false : !config.use_tls}
          />
          <span className="text-sm">{t('settings.fanout.mqtt.verifyTls')}</span>
        </label>
      </div>

      <div className="space-y-2">
        <Label htmlFor="fanout-comm-iata">{t('settings.fanout.community.regionCode')}</Label>
        <Input
          id="fanout-comm-iata"
          type="text"
          maxLength={3}
          placeholder={t('settings.fanout.community.regionPlaceholder')}
          value={(config.iata as string) || ''}
          onChange={(e) => onChange({ ...config, iata: e.target.value.toUpperCase() })}
          className="w-32"
        />
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.fanout.community.regionHelp')}
        </p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="fanout-comm-topic-template">
          {t('settings.fanout.community.packetTopicTemplate')}
        </Label>
        <Input
          id="fanout-comm-topic-template"
          type="text"
          placeholder={DEFAULT_COMMUNITY_PACKET_TOPIC_TEMPLATE}
          value={(config.topic_template as string | undefined) ?? ''}
          onChange={(e) => onChange({ ...config, topic_template: e.target.value })}
        />
        <p className="text-[0.8125rem] text-muted-foreground">
          <Trans
            i18nKey="settings.fanout.community.packetTopicHelp"
            values={{ defaultTemplate: DEFAULT_COMMUNITY_PACKET_TOPIC_TEMPLATE }}
            components={transInline}
          />
        </p>
      </div>
    </div>
  );
}

function MeshRankConfigEditor({
  config,
  onChange,
}: {
  config: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="space-y-3">
      <p className="text-[0.8125rem] text-muted-foreground">{t('settings.fanout.meshrank.help')}</p>

      <div className="rounded-md border border-input bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
        <Trans
          i18nKey="settings.fanout.meshrank.brokerSummary"
          values={{
            host: DEFAULT_MESHRANK_BROKER_HOST,
            port: DEFAULT_MESHRANK_BROKER_PORT,
            transport: DEFAULT_MESHRANK_TRANSPORT,
            auth: DEFAULT_MESHRANK_AUTH_MODE,
            iata: DEFAULT_MESHRANK_IATA,
          }}
          components={transInline}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="fanout-meshrank-topic-template">
          {t('settings.fanout.community.packetTopicTemplate')}
        </Label>
        <Input
          id="fanout-meshrank-topic-template"
          type="text"
          placeholder="meshrank/uplink/B435F6D5F7896B74C6B995FE221C2C1F/{PUBLIC_KEY}/packets"
          value={(config.topic_template as string) || ''}
          onChange={(e) =>
            onChange({
              ...config,
              iata: DEFAULT_MESHRANK_IATA,
              topic_template: e.target.value,
            })
          }
        />
        <p className="text-[0.8125rem] text-muted-foreground">
          <Trans i18nKey="settings.fanout.meshrank.topicHelp" components={transInline} />
        </p>
      </div>
    </div>
  );
}

function LetsMeshConfigEditor({
  config,
  onChange,
  brokerHost,
}: {
  config: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
  brokerHost: string;
}) {
  const { t } = useTranslation();
  return (
    <div className="space-y-3">
      <p className="text-[0.8125rem] text-muted-foreground">{t('settings.fanout.letsmesh.help')}</p>

      <div className="rounded-md border border-input bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
        <Trans
          i18nKey="settings.fanout.letsmesh.brokerSummary"
          values={{
            host: brokerHost,
            port: DEFAULT_COMMUNITY_BROKER_PORT,
            transport: DEFAULT_COMMUNITY_TRANSPORT,
            auth: DEFAULT_COMMUNITY_AUTH_MODE,
          }}
          components={transInline}
        />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-letsmesh-email">{t('settings.fanout.letsmesh.email')}</Label>
          <Input
            id="fanout-letsmesh-email"
            type="email"
            placeholder={t('settings.fanout.community.emailPlaceholder')}
            value={(config.email as string) || ''}
            onChange={(e) =>
              onChange({ ...config, email: e.target.value, broker_host: brokerHost })
            }
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="fanout-letsmesh-iata">{t('settings.fanout.community.regionCode')}</Label>
          <Input
            id="fanout-letsmesh-iata"
            type="text"
            maxLength={3}
            placeholder={t('settings.fanout.community.regionPlaceholder')}
            value={(config.iata as string) || ''}
            onChange={(e) =>
              onChange({
                ...config,
                broker_host: brokerHost,
                token_audience: brokerHost,
                iata: e.target.value.toUpperCase(),
              })
            }
            className="w-32"
          />
        </div>
      </div>
    </div>
  );
}

function BotConfigEditor({
  config,
  onChange,
}: {
  config: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
}) {
  const code = (config.code as string) || '';
  const { t } = useTranslation();
  return (
    <div className="space-y-3">
      <div className="p-3 bg-destructive/10 border border-destructive/30 rounded-md">
        <p className="text-sm text-destructive">
          <Trans i18nKey="settings.fanout.bot.experimental" components={transInline} />
        </p>
      </div>

      <div className="p-3 bg-warning/10 border border-warning/30 rounded-md">
        <p className="text-sm text-warning">
          <Trans i18nKey="settings.fanout.bot.security" components={transInline} />
        </p>
      </div>

      <div className="p-3 bg-warning/10 border border-warning/30 rounded-md">
        <p className="text-sm text-warning">
          <Trans i18nKey="settings.fanout.bot.dontWreck" components={transInline} />
        </p>
      </div>

      <div className="flex items-center justify-between">
        <p className="text-[0.8125rem] text-muted-foreground">
          <Trans i18nKey="settings.fanout.bot.defineFunction" components={transInline} />
        </p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => onChange({ ...config, code: DEFAULT_BOT_CODE })}
        >
          {t('settings.fanout.bot.resetExample')}
        </Button>
      </div>

      <Suspense
        fallback={
          <div className="h-64 md:h-96 rounded-md border border-input bg-code-editor-bg flex items-center justify-center text-muted-foreground">
            {t('settings.fanout.bot.loadingEditor')}
          </div>
        }
      >
        <BotCodeEditor value={code} onChange={(c) => onChange({ ...config, code: c })} />
      </Suspense>

      <div className="text-[0.8125rem] text-muted-foreground space-y-1">
        <p>
          <Trans i18nKey="settings.fanout.bot.available" components={transInline} />
        </p>
        <p>
          <Trans i18nKey="settings.fanout.bot.limits" components={transInline} />
        </p>
        <p>
          <Trans i18nKey="settings.fanout.bot.note" components={transInline} />
        </p>
      </div>
    </div>
  );
}

function MapUploadConfigEditor({
  config,
  onChange,
}: {
  config: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
}) {
  const isDryRun = config.dry_run !== false;
  const [radioLat, setRadioLat] = useState<number | null>(null);
  const [radioLon, setRadioLon] = useState<number | null>(null);

  useEffect(() => {
    api
      .getRadioConfig()
      .then((rc) => {
        setRadioLat(rc.lat ?? 0);
        setRadioLon(rc.lon ?? 0);
      })
      .catch(() => {
        setRadioLat(0);
        setRadioLon(0);
      });
  }, []);

  const radioLatLonConfigured =
    radioLat !== null && radioLon !== null && !(radioLat === 0 && radioLon === 0);
  const { t } = useTranslation();

  return (
    <div className="space-y-3">
      <p className="text-[0.8125rem] text-muted-foreground">
        <Trans
          i18nKey="settings.fanout.map.help"
          components={{
            ...transInline,
            map: (
              <a
                href="https://map.meshcore.io"
                target="_blank"
                rel="noopener noreferrer"
                className="underline hover:text-foreground"
              />
            ),
          }}
        />
      </p>

      <div className="rounded-md border border-warning/50 bg-warning/10 px-3 py-2 text-xs text-warning">
        <Trans
          i18nKey={
            isDryRun ? 'settings.fanout.map.dryRunStatusOn' : 'settings.fanout.map.dryRunStatusOff'
          }
          components={transInline}
        />
      </div>

      <label className="flex items-center gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={isDryRun}
          onChange={(e) => onChange({ ...config, dry_run: e.target.checked })}
          className="h-4 w-4 rounded border-border"
        />
        <div>
          <span className="text-sm font-medium">{t('settings.fanout.map.dryRunLabel')}</span>
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.fanout.map.dryRunHelp')}
          </p>
        </div>
      </label>

      <Separator />

      <div className="space-y-2">
        <Label htmlFor="fanout-map-api-url">{t('settings.fanout.map.apiUrl')}</Label>
        <Input
          id="fanout-map-api-url"
          type="url"
          placeholder="https://map.meshcore.io/api/v1/uploader/node"
          value={(config.api_url as string) || ''}
          onChange={(e) => onChange({ ...config, api_url: e.target.value })}
        />
        <p className="text-[0.8125rem] text-muted-foreground">
          <Trans i18nKey="settings.fanout.map.apiUrlHelp" components={transInline} />
        </p>
      </div>

      <Separator />

      <label className="flex items-center gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={!!config.geofence_enabled}
          onChange={(e) => onChange({ ...config, geofence_enabled: e.target.checked })}
          className="h-4 w-4 rounded border-border"
        />
        <div>
          <span className="text-sm font-medium">{t('settings.fanout.map.enableGeofence')}</span>
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.fanout.map.geofenceHelp')}
          </p>
        </div>
      </label>

      {!!config.geofence_enabled && (
        <div className="space-y-3 pl-7">
          {!radioLatLonConfigured && (
            <div className="rounded-md border border-warning/50 bg-warning/10 px-3 py-2 text-xs text-warning">
              <Trans i18nKey="settings.fanout.map.noCoords" components={transInline} />
            </div>
          )}
          {radioLatLonConfigured && (
            <p className="text-[0.8125rem] text-muted-foreground">
              <Trans
                i18nKey="settings.fanout.map.usingPosition"
                values={{ lat: radioLat?.toFixed(5), lon: radioLon?.toFixed(5) }}
                components={transInline}
              />
            </p>
          )}
          <div className="space-y-2">
            <Label htmlFor="fanout-map-geofence-radius">{t('settings.fanout.map.radius')}</Label>
            <Input
              id="fanout-map-geofence-radius"
              type="number"
              min="0"
              step="any"
              placeholder={t('settings.fanout.map.radiusPlaceholder')}
              value={getOptionalNumberInputValue(config.geofence_radius_km)}
              onChange={(e) =>
                onChange({
                  ...config,
                  geofence_radius_km: parseFloatInputValue(e.target.value),
                })
              }
            />
            <p className="text-[0.8125rem] text-muted-foreground">
              {t('settings.fanout.map.radiusHelp')}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

type ScopeMode = 'all' | 'none' | 'only' | 'except';

function getScopeMode(value: unknown): ScopeMode {
  if (value === 'all') return 'all';
  if (value === 'none') return 'none';
  if (typeof value === 'object' && value !== null) {
    // Check if either channels or contacts uses the {except: [...]} shape
    const obj = value as Record<string, unknown>;
    const ch = obj.channels;
    const co = obj.contacts;
    if (
      (typeof ch === 'object' && ch !== null && !Array.isArray(ch)) ||
      (typeof co === 'object' && co !== null && !Array.isArray(co))
    ) {
      return 'except';
    }
    return 'only';
  }
  return 'all';
}

/** Extract the key list from a filter value, whether it's a plain list or {except: [...]} */
function getFilterKeys(filter: unknown): string[] {
  if (Array.isArray(filter)) return filter as string[];
  if (typeof filter === 'object' && filter !== null && 'except' in filter)
    return ((filter as Record<string, unknown>).except as string[]) ?? [];
  return [];
}

const MAX_SCOPE_PILL_DISPLAY = 32;

interface PillsSearchListItem {
  key: string;
  label: string;
  /** Optional trailing monospace hint (e.g. pubkey prefix) */
  trailing?: string;
}

/**
 * Search-and-pills picker for the generic fanout scope selector.
 * Shows selected items as removable pills (up to MAX_SCOPE_PILL_DISPLAY),
 * a search input, and a scrollable list of filtered items with checkboxes.
 * When more than MAX_SCOPE_PILL_DISPLAY items are selected, the pill row
 * collapses to a single informational badge to keep the interface clean.
 */
function PillsSearchList({
  label,
  labelSuffix,
  items,
  selectedKeys,
  onToggle,
  onAll,
  onNone,
  searchPlaceholder,
  emptyItemsMessage,
  matchKind,
}: {
  label: string;
  labelSuffix: string;
  items: PillsSearchListItem[];
  selectedKeys: string[];
  onToggle: (key: string) => void;
  onAll: () => void;
  onNone: () => void;
  searchPlaceholder: string;
  emptyItemsMessage: string;
  matchKind: 'channels' | 'contacts';
}) {
  const { t } = useTranslation();
  const [search, setSearch] = useState('');
  const searchLower = search.toLowerCase().trim();

  const filtered = useMemo(() => {
    const matches = items.filter((it) => {
      if (!searchLower) return true;
      return (
        it.label.toLowerCase().includes(searchLower) || it.key.toLowerCase().startsWith(searchLower)
      );
    });
    // Selected items sort to top (mirrors the Home Assistant tracked-contacts picker)
    return matches.sort((a, b) => {
      const aSel = selectedKeys.includes(a.key) ? 0 : 1;
      const bSel = selectedKeys.includes(b.key) ? 0 : 1;
      if (aSel !== bSel) return aSel - bSel;
      return a.label.localeCompare(b.label);
    });
  }, [items, searchLower, selectedKeys]);

  const selectedDetails = useMemo(
    () => items.filter((it) => selectedKeys.includes(it.key)),
    [items, selectedKeys]
  );
  const overPillLimit = selectedDetails.length > MAX_SCOPE_PILL_DISPLAY;

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <Label className="text-xs">
          {label} <span className="text-muted-foreground font-normal">({labelSuffix})</span>
        </Label>
        <span className="flex gap-1">
          <button
            type="button"
            className="text-xs text-muted-foreground hover:text-foreground transition-colors"
            onClick={onAll}
          >
            {t('settings.fanout.scope.all')}
          </button>
          <span className="text-xs text-muted-foreground">/</span>
          <button
            type="button"
            className="text-xs text-muted-foreground hover:text-foreground transition-colors"
            onClick={onNone}
          >
            {t('settings.fanout.scope.none')}
          </button>
        </span>
      </div>

      {selectedDetails.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {overPillLimit ? (
            <span className="inline-flex items-center text-[0.6875rem] px-2 py-0.5 rounded-full bg-muted text-muted-foreground">
              {t('settings.fanout.scope.hidePills', { max: MAX_SCOPE_PILL_DISPLAY })}
            </span>
          ) : (
            selectedDetails.map((it) => (
              <span
                key={it.key}
                className="inline-flex items-center gap-1 text-[0.6875rem] px-2 py-0.5 rounded-full bg-primary/10 text-primary"
              >
                {it.label}
                <button
                  type="button"
                  className="ml-0.5 hover:text-destructive transition-colors"
                  onClick={() => onToggle(it.key)}
                  aria-label={t('settings.fanout.scope.removeAria', { name: it.label })}
                >
                  &times;
                </button>
              </span>
            ))
          )}
        </div>
      )}

      {items.length === 0 ? (
        <p className="text-[0.8125rem] text-muted-foreground italic">{emptyItemsMessage}</p>
      ) : (
        <>
          <Input
            type="text"
            placeholder={searchPlaceholder}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-8 text-sm"
          />
          <div className="max-h-48 overflow-y-auto space-y-1 rounded border border-border p-2">
            {filtered.length === 0 ? (
              <p className="text-[0.8125rem] text-muted-foreground italic py-1">
                {t(
                  matchKind === 'channels'
                    ? 'settings.fanout.scope.noChannelsMatch'
                    : 'settings.fanout.scope.noContactsMatch',
                  { query: search }
                )}
              </p>
            ) : (
              filtered.map((it) => (
                <label key={it.key} className="flex items-center gap-2 cursor-pointer text-sm">
                  <input
                    type="checkbox"
                    checked={selectedKeys.includes(it.key)}
                    onChange={() => onToggle(it.key)}
                    className="h-3.5 w-3.5 rounded border-input accent-primary"
                  />
                  <span className="truncate">{it.label}</span>
                  {it.trailing && (
                    <span className="text-[0.625rem] text-muted-foreground ml-auto font-mono shrink-0">
                      {it.trailing}
                    </span>
                  )}
                </label>
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
}

function ScopeSelector({
  scope,
  onChange,
  showRawPackets = false,
}: {
  scope: Record<string, unknown>;
  onChange: (scope: Record<string, unknown>) => void;
  showRawPackets?: boolean;
}) {
  const { t } = useTranslation();
  const [channels, setChannels] = useState<Channel[]>([]);
  const [contacts, setContacts] = useState<Contact[]>([]);

  useEffect(() => {
    api.getChannels().then(setChannels).catch(console.error);

    // Paginate to fetch all contacts (API caps at 1000 per request)
    (async () => {
      const all: Contact[] = [];
      const pageSize = 1000;
      let offset = 0;

      while (true) {
        const page = await api.getContacts(pageSize, offset);
        all.push(...page);
        if (page.length < pageSize) break;
        offset += pageSize;
      }
      setContacts(all);
    })().catch(console.error);
  }, []);

  const messages = scope.messages ?? 'all';
  const rawMode = getScopeMode(messages);
  // When raw packets aren't offered, "none" is not a valid choice — treat as "all"
  const mode = !showRawPackets && rawMode === 'none' ? 'all' : rawMode;
  const isListMode = mode === 'only' || mode === 'except';

  const selectedChannels: string[] =
    isListMode && typeof messages === 'object' && messages !== null
      ? getFilterKeys((messages as Record<string, unknown>).channels)
      : [];
  const selectedContacts: string[] =
    isListMode && typeof messages === 'object' && messages !== null
      ? getFilterKeys((messages as Record<string, unknown>).contacts)
      : [];

  /** Wrap channel/contact key lists in the right shape for the current mode */
  const buildMessages = (chKeys: string[], coKeys: string[]) => {
    if (mode === 'except') {
      return {
        channels: { except: chKeys },
        contacts: { except: coKeys },
      };
    }
    return { channels: chKeys, contacts: coKeys };
  };

  const handleModeChange = (newMode: ScopeMode) => {
    if (newMode === 'all' || newMode === 'none') {
      onChange({ ...scope, messages: newMode });
    } else if (newMode === 'only') {
      onChange({ ...scope, messages: { channels: [], contacts: [] } });
    } else {
      onChange({
        ...scope,
        messages: { channels: { except: [] }, contacts: { except: [] } },
      });
    }
  };

  const toggleChannel = (key: string) => {
    const current = [...selectedChannels];
    const idx = current.indexOf(key);
    if (idx >= 0) current.splice(idx, 1);
    else current.push(key);
    onChange({ ...scope, messages: buildMessages(current, selectedContacts) });
  };

  const toggleContact = (key: string) => {
    const current = [...selectedContacts];
    const idx = current.indexOf(key);
    if (idx >= 0) current.splice(idx, 1);
    else current.push(key);
    onChange({ ...scope, messages: buildMessages(selectedChannels, current) });
  };

  // Exclude repeaters (2), rooms (3), and sensors (4)
  const filteredContacts = contacts.filter((c) => c.type === 0 || c.type === 1);

  const modeDescriptions: Record<ScopeMode, string> = {
    all: t('settings.fanout.scope.allMessages'),
    none: t('settings.fanout.scope.noMessages'),
    only: t('settings.fanout.scope.onlyListed'),
    except: t('settings.fanout.scope.allExcept'),
  };

  const rawEnabled = showRawPackets && scope.raw_packets === 'all';

  // Warn when the effective scope matches nothing
  const messagesEffectivelyNone =
    mode === 'none' ||
    (mode === 'only' && selectedChannels.length === 0 && selectedContacts.length === 0) ||
    (mode === 'except' &&
      channels.length > 0 &&
      filteredContacts.length > 0 &&
      selectedChannels.length >= channels.length &&
      selectedContacts.length >= filteredContacts.length);
  const showEmptyScopeWarning = messagesEffectivelyNone && !rawEnabled;

  const listHint =
    mode === 'only' ? t('settings.fanout.scope.onlyHint') : t('settings.fanout.scope.exceptHint');

  const checkboxLabel =
    mode === 'except' ? t('settings.fanout.scope.exclude') : t('settings.fanout.scope.include');

  const messageModes: ScopeMode[] = showRawPackets
    ? ['all', 'none', 'only', 'except']
    : ['all', 'only', 'except'];

  return (
    <div className="space-y-3">
      <h3 className="text-base font-semibold tracking-tight">{t('settings.fanout.scope.title')}</h3>

      {showRawPackets && (
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={rawEnabled}
            onChange={(e) => onChange({ ...scope, raw_packets: e.target.checked ? 'all' : 'none' })}
            className="h-4 w-4 rounded border-border"
          />
          <span className="text-sm">{t('settings.fanout.scope.forwardRaw')}</span>
        </label>
      )}

      <div className="space-y-1">
        {messageModes.map((m) => (
          <label key={m} className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              name="scope-mode"
              checked={mode === m}
              onChange={() => handleModeChange(m)}
              className="h-4 w-4 accent-primary"
            />
            <span className="text-sm">{modeDescriptions[m]}</span>
          </label>
        ))}
      </div>

      {showEmptyScopeWarning && (
        <div className="rounded-md border border-warning/50 bg-warning/10 px-3 py-2 text-xs text-warning">
          {t('settings.fanout.scope.emptyWarning')}
        </div>
      )}

      {isListMode && (
        <>
          <p className="text-[0.8125rem] text-muted-foreground">{listHint}</p>

          {channels.length > 0 && (
            <PillsSearchList
              label={t('settings.fanout.scope.channels')}
              labelSuffix={checkboxLabel}
              items={channels.map((ch) => ({ key: ch.key, label: ch.name }))}
              selectedKeys={selectedChannels}
              onToggle={toggleChannel}
              onAll={() =>
                onChange({
                  ...scope,
                  messages: buildMessages(
                    channels.map((ch) => ch.key),
                    selectedContacts
                  ),
                })
              }
              onNone={() => onChange({ ...scope, messages: buildMessages([], selectedContacts) })}
              searchPlaceholder={t('settings.fanout.scope.searchChannels', {
                count: channels.length,
              })}
              emptyItemsMessage={t('settings.fanout.scope.noChannels')}
              matchKind="channels"
            />
          )}

          {filteredContacts.length > 0 && (
            <PillsSearchList
              label={t('settings.fanout.scope.contacts')}
              labelSuffix={checkboxLabel}
              items={filteredContacts.map((c) => ({
                key: c.public_key,
                label: c.name || c.public_key.slice(0, 12),
                trailing: c.public_key.slice(0, 12),
              }))}
              selectedKeys={selectedContacts}
              onToggle={toggleContact}
              onAll={() =>
                onChange({
                  ...scope,
                  messages: buildMessages(
                    selectedChannels,
                    filteredContacts.map((c) => c.public_key)
                  ),
                })
              }
              onNone={() => onChange({ ...scope, messages: buildMessages(selectedChannels, []) })}
              searchPlaceholder={t('settings.fanout.scope.searchContacts', {
                count: filteredContacts.length,
              })}
              emptyItemsMessage={t('settings.fanout.scope.noContacts')}
              matchKind="contacts"
            />
          )}
        </>
      )}
    </div>
  );
}

const APPRISE_DEFAULT_DM = '**DM:** {sender_name}: {text} **via:** [{hops_backticked}]';
const APPRISE_DEFAULT_CHANNEL =
  '**{channel_name}:** {sender_name}: {text} **via:** [{hops_backticked}]';
const APPRISE_DEFAULT_DM_PLAIN = 'DM: {sender_name}: {text} via: [{hops}]';
const APPRISE_DEFAULT_CHANNEL_PLAIN = '{channel_name}: {sender_name}: {text} via: [{hops}]';

const APPRISE_SAMPLE_VARS: Record<string, string> = {
  type: 'CHAN',
  text: 'hello world',
  sender_name: 'Alice',
  sender_key: 'a1b2c3d4e5f6',
  channel_name: '#general',
  conversation_key: 'abcdef1234567890',
  hops: '2a, 3b',
  hops_backticked: '`2a`, `3b`',
  hop_count: '2',
  rssi: '-95',
  snr: '6.5',
};

const APPRISE_SAMPLE_VARS_DM: Record<string, string> = {
  ...APPRISE_SAMPLE_VARS,
  type: 'PRIV',
  channel_name: '',
  conversation_key: 'a1b2c3d4e5f6',
};

function appriseApplyFormat(fmt: string, vars: Record<string, string>): string {
  let result = fmt;
  for (const [key, value] of Object.entries(vars)) {
    result = result.split(`{${key}}`).join(value);
  }
  return result;
}

/** Render a markdown-ish string into inline React elements (bold, italic, code). */
function appriseRenderMarkdown(s: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let key = 0;
  // Split on **bold**, __bold__, *italic*, _italic_, and `code` spans.
  // Longer delimiters first so ** and __ match before * and _.
  const parts = s.split(/(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\*[^*]+\*|_[^_]+_)/g);
  for (const part of parts) {
    if (
      (part.startsWith('**') && part.endsWith('**')) ||
      (part.startsWith('__') && part.endsWith('__'))
    ) {
      nodes.push(
        <strong key={key++} className="font-bold">
          {part.slice(2, -2)}
        </strong>
      );
    } else if (
      (part.startsWith('*') && part.endsWith('*')) ||
      (part.startsWith('_') && part.endsWith('_'))
    ) {
      nodes.push(
        <em key={key++} className="italic">
          {part.slice(1, -1)}
        </em>
      );
    } else if (part.startsWith('`') && part.endsWith('`')) {
      nodes.push(
        <code key={key++} className="rounded bg-muted px-1 py-0.5 text-[0.6875rem] font-mono">
          {part.slice(1, -1)}
        </code>
      );
    } else if (part) {
      nodes.push(<span key={key++}>{part}</span>);
    }
  }
  return nodes;
}

function AppriseFormatPreview({
  format,
  vars,
  markdown = true,
}: {
  format: string;
  vars: Record<string, string>;
  markdown?: boolean;
}) {
  const raw = appriseApplyFormat(format, vars);
  const { t } = useTranslation();
  return (
    <div className="rounded-md border border-border bg-muted/30 p-2 space-y-1.5">
      {markdown && (
        <div>
          <span className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
            {t('settings.fanout.apprise.renderedPreview')}
          </span>
          <p className="text-xs break-all">{appriseRenderMarkdown(raw)}</p>
        </div>
      )}
      <div>
        <span className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
          {markdown
            ? t('settings.fanout.apprise.rawPreview')
            : t('settings.fanout.apprise.preview')}
        </span>
        <p className="text-xs font-mono break-all text-muted-foreground">{raw}</p>
      </div>
    </div>
  );
}

function appriseIsDefault(value: unknown, defaultStr: string): boolean {
  if (value == null) return true;
  const s = String(value).trim();
  return s === '' || s === defaultStr;
}

function AppriseConfigEditor({
  config,
  scope,
  onChange,
  onScopeChange,
}: {
  config: Record<string, unknown>;
  scope: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
  onScopeChange: (scope: Record<string, unknown>) => void;
}) {
  const markdown = config.markdown_format !== false;
  const defaultDm = markdown ? APPRISE_DEFAULT_DM : APPRISE_DEFAULT_DM_PLAIN;
  const defaultChan = markdown ? APPRISE_DEFAULT_CHANNEL : APPRISE_DEFAULT_CHANNEL_PLAIN;
  const dmFormat = ((config.body_format_dm as string) || '').trim() || defaultDm;
  const chanFormat = ((config.body_format_channel as string) || '').trim() || defaultChan;
  const { t } = useTranslation();

  return (
    <div className="space-y-3">
      <p className="text-[0.8125rem] text-muted-foreground">
        <Trans
          i18nKey="settings.fanout.apprise.help"
          components={{
            apprise: (
              <a
                href="https://github.com/caronc/apprise"
                target="_blank"
                rel="noopener noreferrer"
                className="underline hover:text-foreground"
              />
            ),
            services: (
              <a
                href="https://github.com/caronc/apprise/wiki#supported-notifications"
                target="_blank"
                rel="noopener noreferrer"
                className="underline hover:text-foreground"
              />
            ),
          }}
        />
      </p>

      <div className="space-y-2">
        <Label htmlFor="fanout-apprise-urls">{t('settings.fanout.apprise.urls')}</Label>
        <textarea
          id="fanout-apprise-urls"
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono min-h-[80px]"
          placeholder={
            'discord://webhook_id/token\nslack://token_a/token_b/token_c\ntgram://bot_token/chat_id'
          }
          value={(config.urls as string) || ''}
          onChange={(e) => onChange({ ...config, urls: e.target.value })}
          rows={4}
        />
        <p className="text-[0.8125rem] text-muted-foreground">
          <Trans i18nKey="settings.fanout.apprise.urlsHelp" components={transInline} />
        </p>
      </div>

      <label className="flex items-center gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={config.preserve_identity !== false}
          onChange={(e) => onChange({ ...config, preserve_identity: e.target.checked })}
          className="h-4 w-4 rounded border-border"
        />
        <div>
          <span className="text-sm">{t('settings.fanout.apprise.preserveIdentity')}</span>
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.fanout.apprise.preserveIdentityHelp')}
          </p>
        </div>
      </label>

      <div className="flex items-center gap-3">
        <input
          id="fanout-apprise-include-outgoing"
          type="checkbox"
          checked={config.include_outgoing === true}
          onChange={(e) => onChange({ ...config, include_outgoing: e.target.checked })}
          className="h-4 w-4 rounded border-border"
          aria-describedby="fanout-apprise-include-outgoing-help"
        />
        <div>
          <label htmlFor="fanout-apprise-include-outgoing" className="text-sm cursor-pointer">
            {t('settings.fanout.apprise.includeOutgoing')}
          </label>
          <p
            id="fanout-apprise-include-outgoing-help"
            className="text-[0.8125rem] text-muted-foreground"
          >
            {t('settings.fanout.apprise.includeOutgoingHelp')}
          </p>
        </div>
      </div>

      <Separator />

      <h3 className="text-base font-semibold tracking-tight">
        {t('settings.fanout.apprise.messageFormat')}
      </h3>

      <label className="flex items-center gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={markdown}
          onChange={(e) => {
            const md = e.target.checked;
            const updates: Record<string, unknown> = { ...config, markdown_format: md };
            const curDm = ((config.body_format_dm as string) || '').trim();
            const curChan = ((config.body_format_channel as string) || '').trim();
            if (md) {
              if (!curDm || curDm === APPRISE_DEFAULT_DM_PLAIN)
                updates.body_format_dm = APPRISE_DEFAULT_DM;
              if (!curChan || curChan === APPRISE_DEFAULT_CHANNEL_PLAIN)
                updates.body_format_channel = APPRISE_DEFAULT_CHANNEL;
            } else {
              if (!curDm || curDm === APPRISE_DEFAULT_DM)
                updates.body_format_dm = APPRISE_DEFAULT_DM_PLAIN;
              if (!curChan || curChan === APPRISE_DEFAULT_CHANNEL)
                updates.body_format_channel = APPRISE_DEFAULT_CHANNEL_PLAIN;
            }
            onChange(updates);
          }}
          className="h-4 w-4 rounded border-border"
        />
        <div>
          <span className="text-sm">{t('settings.fanout.apprise.markdown')}</span>
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.fanout.apprise.markdownHelp')}
          </p>
        </div>
      </label>

      <details className="group">
        <summary className="text-sm font-medium text-foreground cursor-pointer select-none flex items-center gap-1">
          <ChevronDown className="h-3 w-3 transition-transform group-open:rotate-0 -rotate-90" />
          {t('settings.fanout.apprise.availableVars')}
        </summary>
        <div className="mt-2 rounded-md border border-border bg-muted/30 p-2 text-xs space-y-0.5">
          <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
            <code className="text-[0.6875rem] font-mono bg-muted px-1 rounded">{'{text}'}</code>
            <span className="text-muted-foreground">{t('settings.fanout.apprise.varText')}</span>
            <code className="text-[0.6875rem] font-mono bg-muted px-1 rounded">
              {'{sender_name}'}
            </code>
            <span className="text-muted-foreground">
              {t('settings.fanout.apprise.varSenderName')}
            </span>
            <code className="text-[0.6875rem] font-mono bg-muted px-1 rounded">
              {'{sender_key}'}
            </code>
            <span className="text-muted-foreground">
              {t('settings.fanout.apprise.varSenderKey')}
            </span>
            <code className="text-[0.6875rem] font-mono bg-muted px-1 rounded">
              {'{channel_name}'}
            </code>
            <span className="text-muted-foreground">
              {t('settings.fanout.apprise.varChannelName')}
            </span>
            <code className="text-[0.6875rem] font-mono bg-muted px-1 rounded">
              {'{conversation_key}'}
            </code>
            <span className="text-muted-foreground">
              {t('settings.fanout.apprise.varConversationKey')}
            </span>
            <code className="text-[0.6875rem] font-mono bg-muted px-1 rounded">{'{type}'}</code>
            <span className="text-muted-foreground">{t('settings.fanout.apprise.varType')}</span>
            <code className="text-[0.6875rem] font-mono bg-muted px-1 rounded">{'{hops}'}</code>
            <span className="text-muted-foreground">{t('settings.fanout.apprise.varHops')}</span>
            <code className="text-[0.6875rem] font-mono bg-muted px-1 rounded">
              {'{hops_backticked}'}
            </code>
            <span className="text-muted-foreground">
              {t('settings.fanout.apprise.varHopsBackticked')}
            </span>
            <code className="text-[0.6875rem] font-mono bg-muted px-1 rounded">
              {'{hop_count}'}
            </code>
            <span className="text-muted-foreground">
              {t('settings.fanout.apprise.varHopCount')}
            </span>
            <code className="text-[0.6875rem] font-mono bg-muted px-1 rounded">{'{rssi}'}</code>
            <span className="text-muted-foreground">{t('settings.fanout.apprise.varRssi')}</span>
            <code className="text-[0.6875rem] font-mono bg-muted px-1 rounded">{'{snr}'}</code>
            <span className="text-muted-foreground">{t('settings.fanout.apprise.varSnr')}</span>
          </div>
          <p className="text-xs text-muted-foreground mt-1.5">
            {t('settings.fanout.apprise.varsFooter')}
          </p>
        </div>
      </details>

      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label htmlFor="fanout-apprise-fmt-dm">{t('settings.fanout.apprise.dmFormat')}</Label>
          {!appriseIsDefault(config.body_format_dm, defaultDm) && (
            <button
              type="button"
              aria-label={t('settings.fanout.apprise.resetDmAria')}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors"
              onClick={() => onChange({ ...config, body_format_dm: defaultDm })}
            >
              {t('settings.fanout.apprise.resetDefault')}
            </button>
          )}
        </div>
        <textarea
          id="fanout-apprise-fmt-dm"
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono min-h-[56px]"
          placeholder={defaultDm}
          value={(config.body_format_dm as string) ?? ''}
          onChange={(e) => onChange({ ...config, body_format_dm: e.target.value })}
          rows={2}
        />
        <AppriseFormatPreview format={dmFormat} vars={APPRISE_SAMPLE_VARS_DM} markdown={markdown} />
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label htmlFor="fanout-apprise-fmt-chan">
            {t('settings.fanout.apprise.channelFormat')}
          </Label>
          {!appriseIsDefault(config.body_format_channel, defaultChan) && (
            <button
              type="button"
              aria-label={t('settings.fanout.apprise.resetChannelAria')}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors"
              onClick={() => onChange({ ...config, body_format_channel: defaultChan })}
            >
              {t('settings.fanout.apprise.resetDefault')}
            </button>
          )}
        </div>
        <textarea
          id="fanout-apprise-fmt-chan"
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono min-h-[56px]"
          placeholder={defaultChan}
          value={(config.body_format_channel as string) ?? ''}
          onChange={(e) => onChange({ ...config, body_format_channel: e.target.value })}
          rows={2}
        />
        <AppriseFormatPreview format={chanFormat} vars={APPRISE_SAMPLE_VARS} markdown={markdown} />
      </div>

      <Separator />

      <ScopeSelector scope={scope} onChange={onScopeChange} />
    </div>
  );
}

function WebhookConfigEditor({
  config,
  scope,
  onChange,
  onScopeChange,
}: {
  config: Record<string, unknown>;
  scope: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
  onScopeChange: (scope: Record<string, unknown>) => void;
}) {
  const headersStr = JSON.stringify(config.headers ?? {}, null, 2);
  const [headersText, setHeadersText] = useState(headersStr);
  const [headersError, setHeadersError] = useState<string | null>(null);
  const { t } = useTranslation();

  const handleHeadersChange = (text: string) => {
    setHeadersText(text);
    try {
      const parsed = JSON.parse(text);
      if (typeof parsed !== 'object' || Array.isArray(parsed)) {
        setHeadersError(i18n.t('settings.fanout.webhook.mustBeObject'));
        return;
      }
      setHeadersError(null);
      onChange({ ...config, headers: parsed });
    } catch {
      setHeadersError(i18n.t('settings.fanout.webhook.invalidJson'));
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-[0.8125rem] text-muted-foreground">{t('settings.fanout.webhook.help')}</p>

      <div className="space-y-2">
        <Label htmlFor="fanout-webhook-url">{t('settings.fanout.webhook.url')}</Label>
        <Input
          id="fanout-webhook-url"
          type="url"
          placeholder="https://example.com/webhook"
          value={(config.url as string) || ''}
          onChange={(e) => onChange({ ...config, url: e.target.value })}
        />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-webhook-method">{t('settings.fanout.webhook.httpMethod')}</Label>
          <select
            id="fanout-webhook-method"
            value={(config.method as string) || 'POST'}
            onChange={(e) => onChange({ ...config, method: e.target.value })}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          >
            <option value="POST">POST</option>
            <option value="PUT">PUT</option>
            <option value="PATCH">PATCH</option>
          </select>
        </div>
      </div>

      <Separator />

      <div className="space-y-3">
        <h3 className="text-base font-semibold tracking-tight">
          {t('settings.fanout.webhook.hmacTitle')}
        </h3>
        <p className="text-[0.8125rem] text-muted-foreground">
          <Trans i18nKey="settings.fanout.webhook.hmacHelp" components={transInline} />
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="fanout-webhook-hmac-secret">
              {t('settings.fanout.webhook.hmacSecret')}
            </Label>
            <Input
              id="fanout-webhook-hmac-secret"
              type="password"
              placeholder={t('settings.fanout.webhook.hmacSecretPlaceholder')}
              value={(config.hmac_secret as string) || ''}
              onChange={(e) => onChange({ ...config, hmac_secret: e.target.value })}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="fanout-webhook-hmac-header">
              {t('settings.fanout.webhook.signatureHeader')}
            </Label>
            <Input
              id="fanout-webhook-hmac-header"
              type="text"
              placeholder="X-Webhook-Signature"
              value={(config.hmac_header as string) || ''}
              onChange={(e) => onChange({ ...config, hmac_header: e.target.value })}
            />
          </div>
        </div>
      </div>

      <Separator />

      <div className="space-y-2">
        <Label htmlFor="fanout-webhook-headers">{t('settings.fanout.webhook.extraHeaders')}</Label>
        <textarea
          id="fanout-webhook-headers"
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono min-h-[60px]"
          value={headersText}
          onChange={(e) => handleHeadersChange(e.target.value)}
          placeholder='{"Authorization": "Bearer ..."}'
        />
        {headersError && <p className="text-xs text-destructive">{headersError}</p>}
      </div>

      <Separator />

      <ScopeSelector scope={scope} onChange={onScopeChange} />
    </div>
  );
}

function SqsConfigEditor({
  config,
  scope,
  onChange,
  onScopeChange,
}: {
  config: Record<string, unknown>;
  scope: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
  onScopeChange: (scope: Record<string, unknown>) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="space-y-3">
      <p className="text-[0.8125rem] text-muted-foreground">{t('settings.fanout.sqs.help')}</p>

      <div className="rounded-md border border-warning/50 bg-warning/10 px-3 py-2 text-xs text-warning">
        {t('settings.fanout.sqs.warning')}
      </div>

      <div className="space-y-2">
        <Label htmlFor="fanout-sqs-queue-url">{t('settings.fanout.sqs.queueUrl')}</Label>
        <Input
          id="fanout-sqs-queue-url"
          type="url"
          placeholder="https://sqs.us-east-1.amazonaws.com/123456789012/mesh-events"
          value={(config.queue_url as string) || ''}
          onChange={(e) => onChange({ ...config, queue_url: e.target.value })}
        />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-sqs-region">{t('settings.fanout.sqs.region')}</Label>
          <Input
            id="fanout-sqs-region"
            type="text"
            placeholder="us-east-1"
            value={(config.region_name as string) || ''}
            onChange={(e) => onChange({ ...config, region_name: e.target.value })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="fanout-sqs-endpoint">{t('settings.fanout.sqs.endpoint')}</Label>
          <Input
            id="fanout-sqs-endpoint"
            type="url"
            placeholder="http://localhost:4566"
            value={(config.endpoint_url as string) || ''}
            onChange={(e) => onChange({ ...config, endpoint_url: e.target.value })}
          />
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.fanout.sqs.endpointHelp')}
          </p>
        </div>
      </div>

      <Separator />

      <div className="space-y-2">
        <h3 className="text-base font-semibold tracking-tight">
          {t('settings.fanout.sqs.staticCreds')}
        </h3>
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.fanout.sqs.staticCredsHelp')}
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-sqs-access-key">{t('settings.fanout.sqs.accessKey')}</Label>
          <Input
            id="fanout-sqs-access-key"
            type="text"
            value={(config.access_key_id as string) || ''}
            onChange={(e) => onChange({ ...config, access_key_id: e.target.value })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="fanout-sqs-secret-key">{t('settings.fanout.sqs.secretKey')}</Label>
          <Input
            id="fanout-sqs-secret-key"
            type="password"
            value={(config.secret_access_key as string) || ''}
            onChange={(e) => onChange({ ...config, secret_access_key: e.target.value })}
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="fanout-sqs-session-token">{t('settings.fanout.sqs.sessionToken')}</Label>
        <Input
          id="fanout-sqs-session-token"
          type="password"
          value={(config.session_token as string) || ''}
          onChange={(e) => onChange({ ...config, session_token: e.target.value })}
        />
      </div>

      <Separator />

      <ScopeSelector scope={scope} onChange={onScopeChange} showRawPackets />
    </div>
  );
}

export function SettingsFanoutSection({
  health,
  onHealthRefresh,
  className,
}: {
  health: HealthStatus | null;
  onHealthRefresh?: () => Promise<void>;
  className?: string;
}) {
  const [configs, setConfigs] = useState<FanoutConfig[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftType, setDraftType] = useState<DraftType | null>(null);
  const [editConfig, setEditConfig] = useState<Record<string, unknown>>({});
  const [editScope, setEditScope] = useState<Record<string, unknown>>({});
  const [editName, setEditName] = useState('');
  const [inlineEditingId, setInlineEditingId] = useState<string | null>(null);
  const [inlineEditName, setInlineEditName] = useState('');
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [selectedCreateType, setSelectedCreateType] = useState<DraftType | null>(null);
  const [errorDialogState, setErrorDialogState] = useState<{
    integrationName: string;
    error: string;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const { t } = useTranslation();

  const loadConfigs = useCallback(async () => {
    try {
      const data = await api.getFanoutConfigs();
      setConfigs(data);
    } catch (err) {
      console.error('Failed to load fanout configs:', err);
    }
  }, []);

  useEffect(() => {
    loadConfigs();
  }, [loadConfigs]);

  const availableCreateOptions = useMemo(
    () =>
      CREATE_INTEGRATION_DEFINITIONS.filter(
        (definition) => definition.savedType !== 'bot' || !health?.bots_disabled
      ),
    [health?.bots_disabled]
  );

  useEffect(() => {
    if (!createDialogOpen) return;
    if (availableCreateOptions.length === 0) {
      setSelectedCreateType(null);
      return;
    }
    if (
      selectedCreateType &&
      availableCreateOptions.some((option) => option.value === selectedCreateType)
    ) {
      return;
    }
    setSelectedCreateType(availableCreateOptions[0].value);
  }, [createDialogOpen, availableCreateOptions, selectedCreateType]);

  const handleToggleEnabled = async (cfg: FanoutConfig) => {
    try {
      await api.updateFanoutConfig(cfg.id, { enabled: !cfg.enabled });
      await loadConfigs();
      if (onHealthRefresh) await onHealthRefresh();
      toast.success(
        cfg.enabled ? t('settings.fanout.toast.disabled') : t('settings.fanout.toast.enabled')
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t('settings.fanout.toast.failedUpdate'));
    }
  };

  const handleEdit = (cfg: FanoutConfig) => {
    setCreateDialogOpen(false);
    setInlineEditingId(null);
    setInlineEditName('');
    setDraftType(null);
    setEditingId(cfg.id);
    setEditConfig(cfg.config);
    setEditScope(cfg.scope);
    setEditName(cfg.name);
  };

  const handleStartInlineEdit = (cfg: FanoutConfig) => {
    setCreateDialogOpen(false);
    setInlineEditingId(cfg.id);
    setInlineEditName(cfg.name);
  };

  const handleCancelInlineEdit = () => {
    setInlineEditingId(null);
    setInlineEditName('');
  };

  const handleBackToList = () => {
    const shouldConfirm =
      draftType !== null ||
      fanoutDraftHasUnsavedChanges(
        editingId ? (configs.find((c) => c.id === editingId) ?? null) : null,
        {
          name: editName,
          config: editConfig,
          scope: editScope,
        }
      );
    if (shouldConfirm && !confirm(t('settings.fanout.confirm.leaveUnsaved'))) return;
    setEditingId(null);
    setDraftType(null);
  };

  const handleInlineNameSave = async (cfg: FanoutConfig) => {
    const nextName = inlineEditName.trim();
    if (inlineEditingId !== cfg.id) return;
    if (!nextName) {
      toast.error(t('settings.fanout.toast.nameEmpty'));
      handleCancelInlineEdit();
      return;
    }
    if (nextName === cfg.name) {
      handleCancelInlineEdit();
      return;
    }
    try {
      await api.updateFanoutConfig(cfg.id, { name: nextName });
      if (editingId === cfg.id) {
        setEditName(nextName);
      }
      await loadConfigs();
      toast.success(t('settings.fanout.toast.nameUpdated'));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t('settings.fanout.toast.failedUpdateName'));
    } finally {
      handleCancelInlineEdit();
    }
  };

  const handleSave = async (enabled?: boolean) => {
    const currentDraftType = draftType;
    const currentEditingId = editingId;
    if (!currentEditingId && !currentDraftType) return;
    setBusy(true);
    try {
      if (currentDraftType) {
        const recipe = getCreateIntegrationDefinition(currentDraftType);
        await api.createFanoutConfig({
          type: recipe.savedType,
          name: normalizeDraftName(currentDraftType, editName.trim(), configs),
          config: normalizeDraftConfig(currentDraftType, editConfig),
          scope: normalizeDraftScope(currentDraftType, editScope),
          enabled: enabled ?? true,
        });
      } else {
        if (!currentEditingId) {
          throw new Error(t('settings.fanout.toast.missingId'));
        }
        const editingType = configs.find((cfg) => cfg.id === currentEditingId)?.type ?? '';
        const update: Record<string, unknown> = {
          name: editName,
          config: normalizeIntegrationConfigForSave(editingType, editConfig),
          scope: editScope,
        };
        if (enabled !== undefined) update.enabled = enabled;
        await api.updateFanoutConfig(currentEditingId, update);
      }
      setDraftType(null);
      setEditingId(null);
      await loadConfigs();
      if (onHealthRefresh) {
        try {
          await onHealthRefresh();
        } catch (err) {
          console.error('Failed to refresh health after saving fanout config:', err);
        }
      }
      toast.success(
        enabled ? t('settings.fanout.toast.savedEnabled') : t('settings.fanout.toast.saved')
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t('settings.fanout.toast.failedSave'));
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (id: string) => {
    const cfg = configs.find((c) => c.id === id);
    if (!confirm(t('settings.fanout.confirm.delete', { name: cfg?.name ?? '' }))) return;
    try {
      await api.deleteFanoutConfig(id);
      if (editingId === id) setEditingId(null);
      await loadConfigs();
      if (onHealthRefresh) await onHealthRefresh();
      toast.success(t('settings.fanout.toast.deleted'));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t('settings.fanout.toast.failedDelete'));
    }
  };

  const handleAddCreate = (type: DraftType) => {
    const definition = getCreateIntegrationDefinition(type);
    const defaults = cloneDraftDefaults(type);
    setCreateDialogOpen(false);
    setEditingId(null);
    setDraftType(type);
    setEditName(
      definition.nameMode === 'fixed'
        ? fanoutCreateDefaultName(type)
        : getDefaultIntegrationName(definition.savedType, configs)
    );
    setEditConfig(defaults.config);
    setEditScope(defaults.scope);
  };

  const editingConfig = editingId ? configs.find((c) => c.id === editingId) : null;
  const detailType = draftType ?? editingConfig?.type ?? null;
  const isDraft = draftType !== null;
  const configGroups = FANOUT_TYPE_ORDER.map((type) => ({
    type,
    label: fanoutTypeLabel(type),
    configs: configs
      .filter((cfg) => cfg.type === type)
      .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' })),
  })).filter((group) => group.configs.length > 0);

  // Detail view
  if (detailType) {
    return (
      <div className={cn('mx-auto w-full max-w-[800px] space-y-4', className)}>
        <button
          type="button"
          className="inline-flex items-center rounded-md border border-warning/50 bg-warning/10 px-3 py-2 text-sm text-warning transition-colors hover:bg-warning/20"
          onClick={handleBackToList}
        >
          {t('settings.fanout.list.backToList')}
        </button>

        <div className="space-y-2">
          <Label htmlFor="fanout-edit-name">{t('settings.fanout.list.name')}</Label>
          <Input
            id="fanout-edit-name"
            type="text"
            value={editName}
            onChange={(e) => setEditName(e.target.value)}
          />
        </div>

        <div className="text-xs text-muted-foreground">
          {t('settings.fanout.list.type', { type: getDetailTypeLabel(detailType) })}
        </div>

        <Separator />

        {detailType === 'mqtt_private' && (
          <MqttPrivateConfigEditor
            config={editConfig}
            scope={editScope}
            onChange={setEditConfig}
            onScopeChange={setEditScope}
          />
        )}

        {detailType === 'mqtt_ha' && (
          <MqttHaConfigEditor
            config={editConfig}
            scope={editScope}
            onChange={setEditConfig}
            onScopeChange={setEditScope}
          />
        )}

        {detailType === 'mqtt_community' && (
          <MqttCommunityConfigEditor config={editConfig} onChange={setEditConfig} />
        )}

        {detailType === 'mqtt_community_meshrank' && (
          <MeshRankConfigEditor config={editConfig} onChange={setEditConfig} />
        )}

        {detailType === 'mqtt_community_letsmesh_us' && (
          <LetsMeshConfigEditor
            config={editConfig}
            onChange={setEditConfig}
            brokerHost={DEFAULT_COMMUNITY_BROKER_HOST}
          />
        )}

        {detailType === 'mqtt_community_letsmesh_eu' && (
          <LetsMeshConfigEditor
            config={editConfig}
            onChange={setEditConfig}
            brokerHost={DEFAULT_COMMUNITY_BROKER_HOST_EU}
          />
        )}

        {detailType === 'bot' && <BotConfigEditor config={editConfig} onChange={setEditConfig} />}

        {detailType === 'apprise' && (
          <AppriseConfigEditor
            config={editConfig}
            scope={editScope}
            onChange={setEditConfig}
            onScopeChange={setEditScope}
          />
        )}

        {detailType === 'webhook' && (
          <WebhookConfigEditor
            config={editConfig}
            scope={editScope}
            onChange={setEditConfig}
            onScopeChange={setEditScope}
          />
        )}

        {detailType === 'sqs' && (
          <SqsConfigEditor
            config={editConfig}
            scope={editScope}
            onChange={setEditConfig}
            onScopeChange={setEditScope}
          />
        )}

        {detailType === 'map_upload' && (
          <MapUploadConfigEditor config={editConfig} onChange={setEditConfig} />
        )}

        <Separator />

        <div className="flex gap-2">
          <Button
            onClick={() => handleSave(true)}
            disabled={busy}
            className="flex-1 bg-status-connected hover:bg-status-connected/90 text-primary-foreground"
          >
            {busy ? t('settings.fanout.list.saving') : t('settings.fanout.list.saveEnabled')}
          </Button>
          <Button
            variant="secondary"
            onClick={() => handleSave(false)}
            disabled={busy}
            className="flex-1"
          >
            {busy ? t('settings.fanout.list.saving') : t('settings.fanout.list.saveDisabled')}
          </Button>
          {!isDraft && editingConfig && (
            <Button variant="destructive" onClick={() => handleDelete(editingConfig.id)}>
              {t('settings.fanout.list.delete')}
            </Button>
          )}
        </div>
      </div>
    );
  }

  // List view
  return (
    <div className={cn('mx-auto w-full max-w-[800px] space-y-4', className)}>
      <div className="rounded-md border border-warning/50 bg-warning/10 px-4 py-3 text-sm text-warning">
        {t('settings.fanout.list.experimentalBanner')}
      </div>

      {health?.bots_disabled && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {health.bots_disabled_source === 'until_restart'
            ? t('settings.fanout.list.botsDisabledUntilRestart')
            : t('settings.fanout.list.botsDisabled')}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" onClick={() => setCreateDialogOpen(true)}>
          {t('settings.fanout.list.addIntegration')}
        </Button>
      </div>

      <CreateIntegrationDialog
        open={createDialogOpen}
        options={availableCreateOptions}
        selectedType={selectedCreateType}
        onOpenChange={setCreateDialogOpen}
        onSelect={setSelectedCreateType}
        onCreate={() => {
          if (selectedCreateType) {
            handleAddCreate(selectedCreateType);
          }
        }}
      />

      <Dialog
        open={errorDialogState !== null}
        onOpenChange={(open) => {
          if (!open) {
            setErrorDialogState(null);
          }
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader className="border-b border-border px-5 py-4">
            <DialogTitle>
              {errorDialogState
                ? t('settings.fanout.list.errorTitle', { name: errorDialogState.integrationName })
                : t('settings.fanout.list.errorTitleFallback')}
            </DialogTitle>
            <DialogDescription>{t('settings.fanout.list.errorDescription')}</DialogDescription>
          </DialogHeader>
          <div className="px-5 py-4 text-sm text-muted-foreground">
            <p className="whitespace-pre-wrap break-words font-mono text-foreground">
              {errorDialogState?.error}
            </p>
          </div>
        </DialogContent>
      </Dialog>

      {configGroups.length > 0 && (
        <div className="columns-1 gap-4 md:columns-2">
          {configGroups.map((group) => (
            <section
              key={group.type}
              className="mb-4 inline-block w-full break-inside-avoid space-y-2"
              aria-label={t('settings.fanout.list.groupAria', { label: group.label })}
            >
              <div className="px-1 text-sm font-medium text-muted-foreground">{group.label}</div>
              <div className="space-y-2">
                {group.configs.map((cfg) => {
                  const statusEntry = health?.fanout_statuses?.[cfg.id];
                  const status = cfg.enabled ? statusEntry?.status : undefined;
                  const lastError = cfg.enabled ? statusEntry?.last_error : null;
                  const communityConfig = cfg.config as Record<string, unknown>;
                  return (
                    <div
                      key={cfg.id}
                      role="group"
                      aria-label={t('settings.fanout.list.integrationAria', { name: cfg.name })}
                      className="border border-input rounded-md overflow-hidden"
                    >
                      <div className="flex items-center gap-2 px-3 py-2 bg-muted/50">
                        <label
                          className="flex items-center cursor-pointer"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <input
                            type="checkbox"
                            checked={cfg.enabled}
                            onChange={() => handleToggleEnabled(cfg)}
                            className="w-4 h-4 rounded border-input accent-primary"
                            aria-label={t('settings.fanout.list.enableAria', { name: cfg.name })}
                          />
                        </label>

                        <div className="flex-1 min-w-0">
                          {inlineEditingId === cfg.id ? (
                            <Input
                              value={inlineEditName}
                              autoFocus
                              onChange={(e) => setInlineEditName(e.target.value)}
                              onFocus={(e) => e.currentTarget.select()}
                              onBlur={() => void handleInlineNameSave(cfg)}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') {
                                  e.preventDefault();
                                  e.currentTarget.blur();
                                }
                                if (e.key === 'Escape') {
                                  e.preventDefault();
                                  handleCancelInlineEdit();
                                }
                              }}
                              aria-label={t('settings.fanout.list.editNameAria', {
                                name: cfg.name,
                              })}
                              className="h-8"
                            />
                          ) : (
                            <button
                              type="button"
                              className="block max-w-full cursor-text truncate text-left text-sm font-medium hover:text-foreground/80"
                              onClick={() => handleStartInlineEdit(cfg)}
                            >
                              {cfg.name}
                            </button>
                          )}
                        </div>

                        <div
                          className={cn(
                            'w-2 h-2 rounded-full transition-colors',
                            getStatusColor(status, cfg.enabled)
                          )}
                          title={
                            cfg.enabled
                              ? getStatusLabel(status, cfg.type)
                              : t('settings.fanout.status.disabled')
                          }
                          aria-hidden="true"
                        />
                        <span className="text-xs text-muted-foreground hidden sm:inline">
                          {cfg.enabled
                            ? getStatusLabel(status, cfg.type)
                            : t('settings.fanout.status.disabled')}
                        </span>

                        {lastError && (
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="h-6 w-6 px-0"
                            onClick={() =>
                              setErrorDialogState({
                                integrationName: cfg.name,
                                error: lastError,
                              })
                            }
                            aria-label={t('settings.fanout.list.viewErrorAria', { name: cfg.name })}
                            title={t('settings.fanout.list.viewLatestError')}
                          >
                            <Info className="h-3.5 w-3.5" aria-hidden="true" />
                          </Button>
                        )}

                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-6 px-2 text-xs"
                          onClick={() => handleEdit(cfg)}
                        >
                          {t('settings.fanout.list.edit')}
                        </Button>
                      </div>

                      {cfg.type === 'mqtt_community' && (
                        <div className="space-y-1 border-t border-input px-3 py-2 text-xs text-muted-foreground">
                          <div>
                            {t('settings.fanout.list.broker', {
                              summary: formatBrokerSummary(communityConfig, {
                                host: DEFAULT_COMMUNITY_BROKER_HOST,
                                port: DEFAULT_COMMUNITY_BROKER_PORT,
                              }),
                            })}
                          </div>
                          <div className="break-all">
                            {t('settings.fanout.list.topic')}{' '}
                            <code>
                              {(communityConfig.topic_template as string) ||
                                DEFAULT_COMMUNITY_PACKET_TOPIC_TEMPLATE}
                            </code>
                          </div>
                        </div>
                      )}

                      {cfg.type === 'mqtt_private' && (
                        <div className="space-y-1 border-t border-input px-3 py-2 text-xs text-muted-foreground">
                          <div>
                            {t('settings.fanout.list.broker', {
                              summary: formatBrokerSummary(cfg.config as Record<string, unknown>, {
                                host: '',
                                port: 1883,
                              }),
                            })}
                          </div>
                          <div className="break-all">
                            {t('settings.fanout.list.topics')}{' '}
                            <code>
                              {formatPrivateTopicSummary(cfg.config as Record<string, unknown>)}
                            </code>
                          </div>
                        </div>
                      )}

                      {cfg.type === 'webhook' && (
                        <div className="space-y-1 border-t border-input px-3 py-2 text-xs text-muted-foreground">
                          <div className="break-all">
                            {t('settings.fanout.list.url')}{' '}
                            <code>
                              {((cfg.config as Record<string, unknown>).url as string) ||
                                t('settings.fanout.list.notSet')}
                            </code>
                          </div>
                        </div>
                      )}

                      {cfg.type === 'apprise' && (
                        <div className="space-y-1 border-t border-input px-3 py-2 text-xs text-muted-foreground">
                          <div className="break-all">
                            {t('settings.fanout.list.targets')}{' '}
                            <code>
                              {formatAppriseTargets(
                                (cfg.config as Record<string, unknown>).urls as string | undefined
                              )}
                            </code>
                          </div>
                        </div>
                      )}

                      {cfg.type === 'sqs' && (
                        <div className="space-y-1 border-t border-input px-3 py-2 text-xs text-muted-foreground">
                          <div className="break-all">
                            {t('settings.fanout.list.queue')}{' '}
                            <code>
                              {formatSqsQueueSummary(cfg.config as Record<string, unknown>)}
                            </code>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
