import { useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { ChevronRight, Logs, MessageSquare, Send, Settings } from 'lucide-react';
import { Button } from '../ui/button';
import { ColorPicker } from '../ui/color-picker';

const DEFAULT_SIZE = 14;
import { Checkbox } from '../ui/checkbox';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Separator } from '../ui/separator';
import { cn } from '../../lib/utils';
import { ContactAvatar } from '../ContactAvatar';
import {
  captureLastViewedConversationFromHash,
  getReopenLastConversationEnabled,
  setReopenLastConversationEnabled,
} from '../../utils/lastViewedConversation';
import { ThemeSelector } from './ThemeSelector';
import type { ServerLabel } from '../../types';
import { getContrastTextColor } from '../../utils/localLabel';
import {
  LABEL_COLORS,
  LABEL_SIZES,
  normalizeServerLabel,
  serverLabelStyle,
} from '../../utils/serverLabel';
import {
  DISTANCE_UNIT_LABELS,
  DISTANCE_UNITS,
  setSavedDistanceUnit,
} from '../../utils/distanceUnits';
import { useDistanceUnit } from '../../contexts/DistanceUnitContext';
import { useRichPayloads } from '../../contexts/RichPayloadContext';
import { setSavedRenderRichPayloads } from '../../utils/richPayloadPreference';
import { getSavedGiphyApiKey, setSavedGiphyApiKey } from '../../utils/giphyPreference';
import { usePathHopWidth } from '../../contexts/PathHopWidthContext';
import { setSavedShowPathHopWidth } from '../../utils/pathHopWidthPreference';
import {
  DEFAULT_FONT_SCALE,
  FONT_SCALE_SLIDER_STEP,
  MAX_FONT_SCALE,
  MIN_FONT_SCALE,
  getSavedFontScale,
  setSavedFontScale,
} from '../../utils/fontScale';
import { getAutoFocusInputEnabled, setAutoFocusInputEnabled } from '../../utils/autoFocusInput';
import {
  getTextReplaceEnabled,
  setTextReplaceEnabled as saveTextReplaceEnabled,
  getTextReplaceMapJson,
  setTextReplaceMapJson,
  DEFAULT_MAP_JSON,
} from '../../utils/textReplace';
import {
  BATTERY_DISPLAY_CHANGE_EVENT,
  getShowBatteryPercent,
  setShowBatteryPercent as saveBatteryPercent,
  getShowBatteryVoltage,
  setShowBatteryVoltage as saveBatteryVoltage,
} from '../../utils/batteryDisplay';
import {
  STATUS_DOT_PULSE_CHANGE_EVENT,
  getStatusDotPulseEnabled,
  setStatusDotPulseEnabled as saveStatusDotPulse,
} from '../../utils/statusDotPulse';
import {
  APP_LANGUAGES,
  getSavedLanguage,
  setSavedLanguage,
  type AppLanguage,
} from '../../utils/languagePreference';
import i18n from '../../i18n';
import { SettingsGroupHeader } from './settingsPrimitives';

export function SettingsLocalSection({
  serverLabel,
  onPersistServerLabel,
  onPersistTheme,
  className,
}: {
  serverLabel?: ServerLabel;
  /** Stored with the instance: the name describes the server, not this browser. */
  onPersistServerLabel?: (label: ServerLabel) => void;
  /** Stores the theme with the instance so every device reaching it follows. */
  onPersistTheme?: (themeId: string) => void;
  className?: string;
}) {
  const { t } = useTranslation();
  const { distanceUnit, setDistanceUnit } = useDistanceUnit();
  const { renderRichPayloads, setRenderRichPayloads } = useRichPayloads();
  const { showPathHopWidth, setShowPathHopWidth } = usePathHopWidth();
  const [language, setLanguage] = useState(getSavedLanguage);
  const [reopenLastConversation, setReopenLastConversation] = useState(
    getReopenLastConversationEnabled
  );
  const label = normalizeServerLabel(serverLabel);
  const editLabel = (change: Partial<ServerLabel>) =>
    onPersistServerLabel?.({ ...label, ...change });
  const [autoFocusInput, setAutoFocusInput] = useState(getAutoFocusInputEnabled);
  const [batteryPercent, setBatteryPercent] = useState(getShowBatteryPercent);
  const [batteryVoltage, setBatteryVoltage] = useState(getShowBatteryVoltage);
  const [statusDotPulse, setStatusDotPulse] = useState(getStatusDotPulseEnabled);
  const [giphyApiKey, setGiphyApiKey] = useState(getSavedGiphyApiKey);
  const [textReplaceEnabled, setTextReplaceEnabled] = useState(getTextReplaceEnabled);
  const [textReplaceJson, setTextReplaceJson] = useState(getTextReplaceMapJson);
  const [textReplaceError, setTextReplaceError] = useState<string | null>(null);
  const [fontScale, setFontScale] = useState(getSavedFontScale);
  const [fontScaleSlider, setFontScaleSlider] = useState(getSavedFontScale);
  const [fontScaleInput, setFontScaleInput] = useState(() => String(getSavedFontScale()));

  const commitFontScale = (nextScale: number) => {
    const normalized = setSavedFontScale(nextScale);
    setFontScale(normalized);
    setFontScaleSlider(normalized);
    setFontScaleInput(String(normalized));
  };

  const restoreFontScaleInput = () => {
    setFontScaleInput(String(fontScale));
  };

  const handleSliderChange = (nextScale: number) => {
    setFontScaleSlider(nextScale);
    setFontScaleInput(String(nextScale));
  };

  const handleSliderCommit = (nextScale: number) => {
    commitFontScale(nextScale);
  };

  const handleToggleReopenLastConversation = (enabled: boolean) => {
    setReopenLastConversation(enabled);
    setReopenLastConversationEnabled(enabled);
    if (enabled) {
      captureLastViewedConversationFromHash();
    }
  };

  return (
    <div className={className}>
      <p className="text-[0.8125rem] text-muted-foreground">{t('settings.local.deviceOnly')}</p>

      <div className="space-y-3">
        <Label htmlFor="ui-language">{t('language.label')}</Label>
        <select
          id="ui-language"
          value={language}
          onChange={(event) => {
            const next = event.target.value as AppLanguage;
            setLanguage(next);
            setSavedLanguage(next);
            void i18n.changeLanguage(next);
          }}
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
        >
          {APP_LANGUAGES.map((code) => (
            <option key={code} value={code}>
              {t(`language.${code}`)}
            </option>
          ))}
        </select>
        <p className="text-[0.8125rem] text-muted-foreground">{t('language.description')}</p>
      </div>

      <Separator />

      <div className="space-y-1">
        <SettingsGroupHeader title={t('settings.local.colorScheme')} storedOn="server" instant />
        <ThemeSelector onPersist={onPersistTheme} />
        <ThemePreview className="mt-6" />
      </div>

      <Separator />

      <div className="space-y-3">
        <SettingsGroupHeader title={t('settings.local.localLabel')} storedOn="server" instant />
        <Input
          value={label.text}
          onChange={(e) => editLabel({ text: e.target.value })}
          placeholder={t('settings.local.localLabelPlaceholder')}
          aria-label={t('settings.local.localLabelText')}
          maxLength={60}
        />

        {label.text.trim() && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <select
                value={label.size_px}
                onChange={(e) => editLabel({ size_px: Number(e.target.value) })}
                aria-label={t('settings.local.labelSize')}
                className="h-9 rounded-md border border-input bg-background px-2 text-sm"
              >
                {LABEL_SIZES.map((size) => (
                  <option key={size} value={size}>
                    {size === DEFAULT_SIZE
                      ? t('settings.local.labelSizeDefault', { size })
                      : t('settings.local.labelSizeValue', { size })}
                  </option>
                ))}
              </select>
              <select
                value={label.font}
                onChange={(e) => editLabel({ font: e.target.value as ServerLabel['font'] })}
                aria-label={t('settings.local.labelFont')}
                className="h-9 rounded-md border border-input bg-background px-2 text-sm"
              >
                <option value="system">{t('settings.local.labelFontSystem')}</option>
                <option value="serif">{t('settings.local.labelFontSerif')}</option>
                <option value="mono">{t('settings.local.labelFontMono')}</option>
              </select>
              <ColorPicker
                value={label.color}
                onChange={(color) => editLabel({ color })}
                presets={LABEL_COLORS}
                label={t('settings.local.localLabelColor')}
                presetsLabel={t('settings.local.labelColorPresets')}
                customLabel={t('settings.local.labelColorCustom')}
                hueLabel={t('settings.local.labelColorHue')}
                lightnessLabel={t('settings.local.labelColorLightness')}
                hexLabel={t('settings.local.labelColorHex')}
              />
              <Button
                type="button"
                variant={label.bold ? 'default' : 'outline'}
                size="sm"
                aria-pressed={label.bold}
                onClick={() => editLabel({ bold: !label.bold })}
                className="font-bold"
              >
                {t('settings.local.labelBold')}
              </Button>
              <Button
                type="button"
                variant={label.italic ? 'default' : 'outline'}
                size="sm"
                aria-pressed={label.italic}
                onClick={() => editLabel({ italic: !label.italic })}
                className="italic"
              >
                {t('settings.local.labelItalic')}
              </Button>
            </div>

            <div
              data-testid="server-label-preview"
              style={{
                ...serverLabelStyle(label),
                backgroundColor: label.color,
                color: getContrastTextColor(label.color),
              }}
              className="truncate rounded-md text-center leading-tight"
            >
              {label.text}
            </div>
          </div>
        )}

        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.local.localLabelHelp')}
        </p>
      </div>

      <Separator />

      <div className="space-y-3">
        <Label htmlFor="distance-units">{t('settings.local.distanceUnits')}</Label>
        <select
          id="distance-units"
          value={distanceUnit}
          onChange={(event) => {
            const nextUnit = event.target.value as (typeof DISTANCE_UNITS)[number];
            setSavedDistanceUnit(nextUnit);
            setDistanceUnit(nextUnit);
          }}
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
        >
          {DISTANCE_UNITS.map((unit) => (
            <option key={unit} value={unit}>
              {t(`settings.local.units.${unit}`, { defaultValue: DISTANCE_UNIT_LABELS[unit] })}
            </option>
          ))}
        </select>
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.local.distanceUnitsHelp')}
        </p>
      </div>

      <Separator />

      <div className="space-y-3">
        <SettingsGroupHeader title={t('settings.local.uiTweaks')} storedOn="browser" instant />

        <div className="space-y-2">
          <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
            <Checkbox
              id="reopen-last"
              checked={reopenLastConversation}
              onCheckedChange={(checked) => handleToggleReopenLastConversation(checked === true)}
              className="mt-0.5"
            />
            <div className="space-y-1">
              <Label htmlFor="reopen-last">{t('settings.local.reopenLast')}</Label>
              <p className="text-[0.8125rem] text-muted-foreground">
                {t('settings.local.reopenLastHelp')}
              </p>
            </div>
          </div>

          <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
            <Checkbox
              id="auto-focus-input"
              checked={autoFocusInput}
              onCheckedChange={(checked) => {
                const v = checked === true;
                setAutoFocusInput(v);
                setAutoFocusInputEnabled(v);
              }}
              className="mt-0.5"
            />
            <div className="space-y-1">
              <Label htmlFor="auto-focus-input">{t('settings.local.autoFocus')}</Label>
              <p className="text-[0.8125rem] text-muted-foreground">
                {t('settings.local.autoFocusHelp')}
              </p>
            </div>
          </div>

          <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
            <Checkbox
              id="battery-percent"
              checked={batteryPercent}
              onCheckedChange={(checked) => {
                const v = checked === true;
                setBatteryPercent(v);
                saveBatteryPercent(v);
                window.dispatchEvent(new Event(BATTERY_DISPLAY_CHANGE_EVENT));
              }}
              className="mt-0.5"
            />
            <div className="space-y-1">
              <Label htmlFor="battery-percent">{t('settings.local.batteryPercent')}</Label>
              <p className="text-[0.8125rem] text-muted-foreground">
                {t('settings.local.batteryPercentHelp')}
              </p>
            </div>
          </div>

          <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
            <Checkbox
              id="battery-voltage"
              checked={batteryVoltage}
              onCheckedChange={(checked) => {
                const v = checked === true;
                setBatteryVoltage(v);
                saveBatteryVoltage(v);
                window.dispatchEvent(new Event(BATTERY_DISPLAY_CHANGE_EVENT));
              }}
              className="mt-0.5"
            />
            <div className="space-y-1">
              <Label htmlFor="battery-voltage">{t('settings.local.batteryVoltage')}</Label>
              <p className="text-[0.8125rem] text-muted-foreground">
                {t('settings.local.batteryVoltageHelp')}
              </p>
            </div>
          </div>

          <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
            <Checkbox
              id="status-dot-pulse"
              checked={statusDotPulse}
              onCheckedChange={(checked) => {
                const v = checked === true;
                setStatusDotPulse(v);
                saveStatusDotPulse(v);
                window.dispatchEvent(new Event(STATUS_DOT_PULSE_CHANGE_EVENT));
              }}
              className="mt-0.5"
            />
            <div className="space-y-1">
              <Label htmlFor="status-dot-pulse">{t('settings.local.statusDotPulse')}</Label>
              <p className="text-[0.8125rem] text-muted-foreground">
                {t('settings.local.statusDotPulseHelp')}
              </p>
            </div>
          </div>

          <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
            <Checkbox
              id="render-rich-payloads"
              checked={renderRichPayloads}
              onCheckedChange={(checked) => {
                const v = checked === true;
                setRenderRichPayloads(v);
                setSavedRenderRichPayloads(v);
              }}
              className="mt-0.5"
            />
            <div className="space-y-1">
              <Label htmlFor="render-rich-payloads">{t('settings.local.richPayloads')}</Label>
              <p className="text-[0.8125rem] text-muted-foreground">
                <Trans
                  i18nKey="settings.local.richPayloadsHelp"
                  components={{ code: <code className="text-[0.75rem]" /> }}
                />
              </p>
            </div>
          </div>

          <div className="space-y-2 rounded-md border border-border/60 p-3">
            <Label htmlFor="giphy-api-key">{t('settings.giphyKey')}</Label>
            <Input
              id="giphy-api-key"
              type="password"
              autoComplete="off"
              value={giphyApiKey}
              onChange={(event) => {
                const next = event.target.value;
                setGiphyApiKey(next);
                setSavedGiphyApiKey(next);
              }}
            />
            <p className="text-[0.8125rem] text-muted-foreground">{t('settings.giphyKeyHelp')}</p>
          </div>

          <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
            <Checkbox
              id="show-path-hop-width"
              checked={showPathHopWidth}
              onCheckedChange={(checked) => {
                const v = checked === true;
                setShowPathHopWidth(v);
                setSavedShowPathHopWidth(v);
              }}
              className="mt-0.5"
            />
            <div className="space-y-1">
              <Label htmlFor="show-path-hop-width">{t('settings.local.pathHopWidth')}</Label>
              <p className="text-[0.8125rem] text-muted-foreground">
                <Trans
                  i18nKey="settings.local.pathHopWidthHelp"
                  components={{ code: <code className="text-[0.75rem]" /> }}
                />
              </p>
            </div>
          </div>

          <div className="rounded-md border border-border/60 p-3 space-y-2">
            <div className="flex items-start gap-3">
              <Checkbox
                id="text-replace"
                checked={textReplaceEnabled}
                onCheckedChange={(checked) => {
                  const v = checked === true;
                  setTextReplaceEnabled(v);
                  saveTextReplaceEnabled(v);
                }}
                className="mt-0.5"
              />
              <div className="space-y-1">
                <Label htmlFor="text-replace">{t('settings.local.textReplace')}</Label>
                <p className="text-[0.8125rem] text-muted-foreground">
                  {t('settings.local.textReplaceHelp')}
                </p>
              </div>
            </div>
            {textReplaceEnabled && (
              <div className="space-y-2 pl-7">
                <textarea
                  value={textReplaceJson}
                  onChange={(e) => {
                    const val = e.target.value;
                    setTextReplaceJson(val);
                    setTextReplaceError(setTextReplaceMapJson(val));
                  }}
                  spellCheck={false}
                  rows={10}
                  className={cn(
                    'w-full rounded-md border bg-background px-3 py-2 text-sm font-mono',
                    textReplaceError ? 'border-destructive' : 'border-input'
                  )}
                  aria-label={t('settings.local.textReplaceMap')}
                />
                {textReplaceError && (
                  <p className="text-xs text-destructive">
                    {t('settings.local.textReplaceUnsaved', { error: textReplaceError })}
                  </p>
                )}
                <button
                  type="button"
                  onClick={() => {
                    setTextReplaceJson(DEFAULT_MAP_JSON);
                    setTextReplaceMapJson(DEFAULT_MAP_JSON);
                    setTextReplaceError(null);
                  }}
                  className="inline-flex h-8 items-center justify-center rounded-md border border-input px-3 text-sm font-medium transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {t('settings.local.textReplaceReset')}
                </button>
              </div>
            )}
          </div>
        </div>

        <div className="space-y-3">
          <Label htmlFor="font-scale-input">{t('settings.local.fontSize')}</Label>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <input
              type="range"
              min={MIN_FONT_SCALE}
              max={MAX_FONT_SCALE}
              step={FONT_SCALE_SLIDER_STEP}
              value={fontScaleSlider}
              onChange={(event) => handleSliderChange(Number(event.target.value))}
              onMouseUp={(event) => handleSliderCommit(Number(event.currentTarget.value))}
              onTouchEnd={(event) => handleSliderCommit(Number(event.currentTarget.value))}
              onKeyUp={(event) => handleSliderCommit(Number(event.currentTarget.value))}
              onBlur={(event) => handleSliderCommit(Number(event.currentTarget.value))}
              aria-label={t('settings.local.fontSizeSlider')}
              className="w-full accent-primary sm:flex-1"
            />
            <div className="flex items-center gap-2 sm:w-40">
              <Input
                id="font-scale-input"
                type="number"
                inputMode="decimal"
                min={MIN_FONT_SCALE}
                max={MAX_FONT_SCALE}
                step="any"
                value={fontScaleInput}
                onChange={(event) => {
                  const nextValue = event.target.value;
                  setFontScaleInput(nextValue);

                  if (nextValue === '') {
                    return;
                  }

                  if (event.target.validity.valid && Number.isFinite(event.target.valueAsNumber)) {
                    commitFontScale(event.target.valueAsNumber);
                  }
                }}
                onBlur={() => {
                  const parsed = Number.parseFloat(fontScaleInput);
                  if (!Number.isFinite(parsed)) {
                    restoreFontScaleInput();
                    return;
                  }
                  commitFontScale(parsed);
                }}
                onKeyDown={(event) => {
                  if (event.key !== 'Enter') {
                    return;
                  }
                  event.preventDefault();
                  const parsed = Number.parseFloat(fontScaleInput);
                  if (!Number.isFinite(parsed)) {
                    restoreFontScaleInput();
                    return;
                  }
                  commitFontScale(parsed);
                }}
                aria-label={t('settings.local.fontSizePercent')}
              />
              <span className="text-sm text-muted-foreground">%</span>
            </div>
            <button
              type="button"
              onClick={() => commitFontScale(DEFAULT_FONT_SCALE)}
              className="inline-flex h-9 items-center justify-center rounded-md border border-input px-3 text-sm font-medium transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
              disabled={fontScale === DEFAULT_FONT_SCALE}
            >
              {t('settings.local.reset')}
            </button>
          </div>
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.local.fontSizeHelp')}
          </p>
        </div>
      </div>
    </div>
  );
}

function ThemePreview({ className }: { className?: string }) {
  const { t } = useTranslation();
  const [showStyleRef, setShowStyleRef] = useState(false);

  return (
    <div className={`rounded-lg border border-border bg-card p-3 ${className ?? ''}`}>
      <p className="text-xs text-muted-foreground mb-3">{t('settings.local.preview.intro')}</p>

      <div className="space-y-2">
        <PreviewBanner className="border border-status-connected/30 bg-status-connected/15 text-status-connected">
          {t('settings.local.preview.connected')}
        </PreviewBanner>
        <PreviewBanner className="border border-warning/50 bg-warning/10 text-warning">
          {t('settings.local.preview.warning')}
        </PreviewBanner>
        <PreviewBanner className="border border-destructive/30 bg-destructive/10 text-destructive">
          {t('settings.local.preview.error')}
        </PreviewBanner>
      </div>

      <div className="mt-4 space-y-2">
        <PreviewMessage
          sender="Alice"
          bubbleClassName="bg-msg-incoming text-foreground"
          text={t('settings.local.preview.helloMesh')}
        />
        <PreviewMessage
          sender={t('settings.local.preview.you')}
          alignRight
          bubbleClassName="bg-msg-outgoing text-foreground"
          text={t('settings.local.preview.hiThere')}
        />
      </div>

      <div className="mt-4 rounded-md border border-border bg-background p-2">
        <p className="mb-2 text-[0.6875rem] font-medium text-muted-foreground">
          {t('settings.local.preview.sidebar')}
        </p>
        <div className="space-y-1">
          <PreviewSidebarRow
            active
            leading={
              <span
                className="flex h-6 w-6 items-center justify-center rounded-md bg-primary/10 text-primary"
                aria-hidden="true"
              >
                <Logs className="h-3.5 w-3.5" />
              </span>
            }
            label={t('settings.local.preview.packetFeed')}
          />
          <PreviewSidebarRow
            leading={<ContactAvatar name="Alice" publicKey={'ab'.repeat(32)} size={24} />}
            label="Alice"
            badge={
              <span className="rounded-full bg-badge-unread/90 px-1.5 py-0.5 text-[0.625rem] font-semibold text-badge-unread-foreground">
                3
              </span>
            }
          />
          <PreviewSidebarRow
            leading={<ContactAvatar name="Mesh Ops" publicKey={'cd'.repeat(32)} size={24} />}
            label="Mesh Ops"
            badge={
              <span className="rounded-full bg-badge-mention px-1.5 py-0.5 text-[0.625rem] font-semibold text-badge-mention-foreground">
                @2
              </span>
            }
          />
        </div>
      </div>

      {/* ── Style Reference (collapsible) ── */}
      <button
        type="button"
        onClick={() => setShowStyleRef((v) => !v)}
        className="mt-4 flex w-full items-center gap-1.5 text-[0.6875rem] font-medium text-muted-foreground hover:text-foreground transition-colors"
      >
        <ChevronRight
          className={cn('h-3.5 w-3.5 transition-transform', showStyleRef && 'rotate-90')}
        />
        {t('settings.local.preview.styleRef')}
      </button>

      {showStyleRef && (
        <>
          {/* ── Text Hierarchy ── */}
          <PreviewSection title={t('settings.local.preview.textHierarchy')}>
            <div className="space-y-2">
              <PreviewTextRow
                classes="text-xl font-semibold"
                label="text-xl font-semibold"
                desc={t('settings.local.preview.hero')}
              />
              <PreviewTextRow
                classes="text-lg font-semibold"
                label="text-lg font-semibold"
                desc={t('settings.local.preview.sheetTitle')}
              />
              <PreviewTextRow
                classes="text-base font-semibold tracking-tight"
                label="text-base font-semibold tracking-tight"
                desc={t('settings.local.preview.sectionTitle')}
              />
              <PreviewTextRow
                classes="text-sm"
                label="text-sm"
                desc={t('settings.local.preview.bodyText')}
              />
              <PreviewTextRow
                classes="text-[0.8125rem] text-muted-foreground"
                label="text-[0.8125rem] text-muted-foreground"
                desc={t('settings.local.preview.helperText')}
              />
              <PreviewTextRow
                classes="text-[0.6875rem] text-muted-foreground"
                label="text-[0.6875rem] text-muted-foreground"
                desc={t('settings.local.preview.metadata')}
              />
              <div>
                <p className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
                  {t('settings.local.preview.metadataLabel')}
                </p>
                <p className="text-[0.625rem] text-muted-foreground/60 mt-0.5">
                  text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium
                </p>
              </div>
            </div>
          </PreviewSection>

          {/* ── Mono Text ── */}
          <PreviewSection title={t('settings.local.preview.monoText')}>
            <div className="space-y-1.5">
              <div>
                <p className="text-xs font-mono text-muted-foreground">
                  a1b2c3d4e5f6...7890abcdef01
                </p>
                <p className="text-[0.625rem] text-muted-foreground/60">
                  text-xs font-mono — {t('settings.local.preview.keysIds')}
                </p>
              </div>
              <div>
                <p className="text-[0.6875rem] font-mono">1h 23m 45s uptime</p>
                <p className="text-[0.625rem] text-muted-foreground/60">
                  text-[0.6875rem] font-mono — {t('settings.local.preview.metadataMono')}
                </p>
              </div>
              <div>
                <p className="text-sm font-mono">$ req_status_sync 0xA1B2...</p>
                <p className="text-[0.625rem] text-muted-foreground/60">
                  text-sm font-mono — {t('settings.local.preview.consoleCode')}
                </p>
              </div>
            </div>
          </PreviewSection>

          {/* ── Badges ── */}
          <PreviewSection title={t('settings.local.preview.badges')}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[0.625rem] uppercase tracking-wider px-1.5 py-0.5 rounded bg-muted text-muted-foreground font-medium">
                {t('settings.local.preview.hashtag')}
              </span>
              <span className="text-[0.625rem] uppercase tracking-wider px-1.5 py-0.5 rounded bg-muted text-muted-foreground font-medium">
                {t('settings.local.preview.repeater')}
              </span>
              <span className="text-[0.625rem] uppercase tracking-wider px-1.5 py-0.5 rounded bg-primary/10 text-primary font-medium">
                {t('settings.local.preview.onRadio')}
              </span>
              <span className="rounded-full bg-badge-unread/90 px-1.5 py-0.5 text-[0.625rem] font-semibold text-badge-unread-foreground">
                3
              </span>
              <span className="rounded-full bg-badge-mention px-1.5 py-0.5 text-[0.625rem] font-semibold text-badge-mention-foreground">
                @2
              </span>
            </div>
            <p className="text-[0.625rem] text-muted-foreground/60 mt-1.5">
              Muted: bg-muted &middot; Primary: bg-primary/10 &middot; Unread/Mention: bg-badge-*
            </p>
          </PreviewSection>

          {/* ── Buttons ── */}
          <PreviewSection title={t('settings.local.preview.buttons')}>
            <div className="space-y-3">
              <div>
                <p className="text-[0.625rem] text-muted-foreground/60 mb-1.5">
                  {t('settings.local.preview.standardVariants')}
                </p>
                <div className="flex flex-wrap gap-1.5">
                  <Button size="sm">{t('settings.local.preview.default')}</Button>
                  <Button size="sm" variant="outline">
                    {t('settings.local.preview.outline')}
                  </Button>
                  <Button size="sm" variant="secondary">
                    {t('settings.local.preview.secondary')}
                  </Button>
                  <Button size="sm" variant="destructive">
                    {t('settings.local.preview.destructive')}
                  </Button>
                  <Button size="sm" variant="ghost">
                    {t('settings.local.preview.ghost')}
                  </Button>
                  <Button size="icon" variant="outline">
                    <Settings className="h-4 w-4" />
                  </Button>
                  <Button size="icon" variant="outline">
                    <Send className="h-4 w-4" />
                  </Button>
                </div>
              </div>
              <div>
                <p className="text-[0.625rem] text-muted-foreground/60 mb-1.5">
                  {t('settings.local.preview.semanticOutline')}
                </p>
                <div className="flex flex-wrap gap-1.5">
                  <Button
                    size="sm"
                    variant="outline"
                    className="border-destructive/50 text-destructive hover:bg-destructive/10"
                  >
                    {t('settings.local.preview.danger')}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="border-warning/50 text-warning hover:bg-warning/10"
                  >
                    {t('settings.local.preview.warning')}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="border-green-600/50 text-green-600 hover:bg-green-600/10"
                  >
                    {t('settings.local.preview.success')}
                  </Button>
                </div>
              </div>
              <div>
                <p className="text-[0.625rem] text-muted-foreground/60 mb-1.5">
                  {t('settings.local.preview.metricPills')}
                </p>
                <div className="flex gap-1">
                  {[
                    t('settings.local.preview.voltage'),
                    t('settings.local.preview.noiseFloor'),
                    t('settings.local.preview.packets'),
                  ].map((label, i) => (
                    <button
                      key={label}
                      type="button"
                      className={cn(
                        'text-[0.6875rem] px-2 py-0.5 rounded transition-colors',
                        i === 0
                          ? 'bg-primary text-primary-foreground'
                          : 'text-muted-foreground hover:text-foreground hover:bg-accent'
                      )}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </PreviewSection>

          {/* ── Clickable Text ── */}
          <PreviewSection title={t('settings.local.preview.clickable')}>
            <div className="space-y-1.5">
              <span
                role="button"
                tabIndex={0}
                className="text-xs font-mono text-muted-foreground cursor-pointer hover:text-primary transition-colors block"
              >
                a1b2c3d4e5f6 {t('settings.local.preview.clickToCopy')}
              </span>
              <span
                role="button"
                tabIndex={0}
                className="text-sm cursor-pointer underline underline-offset-2 decoration-muted-foreground/50 hover:text-primary transition-colors"
              >
                {t('settings.local.preview.navLink')}
              </span>
            </div>
            <p className="text-[0.625rem] text-muted-foreground/60 mt-1.5">
              cursor-pointer hover:text-primary transition-colors — use role=&quot;button&quot; +
              tabIndex
            </p>
          </PreviewSection>

          {/* ── Inline Alerts ── */}
          <PreviewSection title={t('settings.local.preview.inlineAlerts')}>
            <div className="space-y-1.5">
              <div className="rounded-md border border-info/30 bg-info/10 px-3 py-2 text-xs text-info">
                {t('settings.local.preview.infoAlert')}
              </div>
              <div className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
                {t('settings.local.preview.warnAlert')}
              </div>
              <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                {t('settings.local.preview.errorAlert')}
              </div>
            </div>
          </PreviewSection>
        </>
      )}
    </div>
  );
}

function PreviewSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-4 rounded-md border border-border bg-background p-2">
      <p className="mb-2 text-[0.6875rem] font-medium text-muted-foreground">{title}</p>
      {children}
    </div>
  );
}

function PreviewTextRow({
  classes,
  label,
  desc,
}: {
  classes: string;
  label: string;
  desc: string;
}) {
  const { t } = useTranslation();
  return (
    <div>
      <p className={classes}>{t('settings.local.preview.sampleText')}</p>
      <p className="text-[0.625rem] text-muted-foreground/60">
        {label} — {desc}
      </p>
    </div>
  );
}

function PreviewBanner({ children, className }: { children: React.ReactNode; className: string }) {
  return <div className={`rounded-md px-3 py-2 text-xs ${className}`}>{children}</div>;
}

function PreviewMessage({
  sender,
  text,
  bubbleClassName,
  alignRight = false,
}: {
  sender: string;
  text: string;
  bubbleClassName: string;
  alignRight?: boolean;
}) {
  return (
    <div className={`flex ${alignRight ? 'justify-end' : 'justify-start'}`}>
      <div className={`max-w-[85%] ${alignRight ? 'items-end' : 'items-start'} flex flex-col`}>
        <span className="mb-1 text-[0.6875rem] text-muted-foreground">{sender}</span>
        <div className={`rounded-2xl px-3 py-2 text-sm break-words ${bubbleClassName}`}>{text}</div>
      </div>
    </div>
  );
}

function PreviewSidebarRow({
  leading,
  label,
  badge,
  active = false,
}: {
  leading: React.ReactNode;
  label: string;
  badge?: React.ReactNode;
  active?: boolean;
}) {
  return (
    <div
      data-active={active ? 'true' : undefined}
      className={`sidebar-action-row flex items-center gap-2 rounded-md border-l-2 px-3 py-2 text-[0.8125rem] ${
        active ? 'border-l-primary bg-accent text-foreground' : 'border-l-transparent'
      }`}
    >
      <span className="sidebar-tool-icon" aria-hidden="true">
        {leading}
      </span>
      <span className={`sidebar-tool-label min-w-0 flex-1 truncate ${active ? 'font-medium' : ''}`}>
        {label}
      </span>
      {badge}
      {!badge && (
        <span className="sidebar-tool-icon" aria-hidden="true">
          <MessageSquare className="h-3.5 w-3.5" />
        </span>
      )}
    </div>
  );
}
