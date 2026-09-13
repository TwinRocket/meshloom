import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api, formatApiError } from '../../api';
import type { RadioProxyStatus, RadioProxyUpdate } from '../../types';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { toast } from '../ui/sonner';

const EMPTY: RadioProxyStatus = {
  enabled: false,
  bind: '0.0.0.0',
  port: 5001,
  max_clients: 8,
  listening: false,
  client_count: 0,
  dropped_messages: 0,
  dropped_logs: 0,
  last_error: null,
  instance_id: '',
  model: '',
};

export function SettingsProxySection({ className }: { className?: string }) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<RadioProxyStatus>(EMPTY);
  const [bind, setBind] = useState(EMPTY.bind);
  const [port, setPort] = useState(String(EMPTY.port));
  const [maxClients, setMaxClients] = useState(String(EMPTY.max_clients));
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const applyStatus = (next: RadioProxyStatus) => {
    setStatus(next);
    setEnabled(next.enabled);
    setBind(next.bind);
    setPort(String(next.port));
    setMaxClients(String(next.max_clients));
  };

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const next = await api.getRadioProxy();
        if (!cancelled) applyStatus(next);
      } catch (err) {
        if (!cancelled) setLoadError(formatApiError(err, t) || t('settings.proxy.loadFailed'));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [t]);

  const handleSave = async () => {
    setBusy(true);
    setLoadError(null);
    try {
      const parsedPort = parseInt(port, 10);
      const parsedMax = parseInt(maxClients, 10);
      const body: RadioProxyUpdate = {
        enabled,
        bind: bind.trim() || '0.0.0.0',
        port: Number.isNaN(parsedPort) ? 5001 : parsedPort,
        max_clients: Number.isNaN(parsedMax) ? 8 : parsedMax,
      };
      const next = await api.updateRadioProxy(body);
      applyStatus(next);
      toast.success(t('settings.proxy.saved'));
    } catch (err) {
      setLoadError(formatApiError(err, t) || t('settings.proxy.saveFailed'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={className}>
      <h3 className="text-base font-semibold tracking-tight">{t('settings.proxy.title')}</h3>
      <p className="mt-1 text-[0.8125rem] text-muted-foreground">{t('settings.proxy.help')}</p>
      <p className="mt-2 text-[0.8125rem] text-muted-foreground">{t('settings.proxy.warning')}</p>
      <p className="mt-2 text-[0.8125rem] text-muted-foreground">
        {t('settings.proxy.identityHelp')}
      </p>

      <div className="mt-4 flex items-center gap-2">
        <Checkbox
          id="radio-proxy-enabled"
          checked={enabled}
          onCheckedChange={(value) => setEnabled(value === true)}
        />
        <Label htmlFor="radio-proxy-enabled">{t('settings.proxy.enabled')}</Label>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <div className="space-y-1.5">
          <Label htmlFor="radio-proxy-bind">{t('settings.proxy.bind')}</Label>
          <Input
            id="radio-proxy-bind"
            value={bind}
            onChange={(event) => setBind(event.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="radio-proxy-port">{t('settings.proxy.port')}</Label>
          <Input
            id="radio-proxy-port"
            value={port}
            onChange={(event) => setPort(event.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="radio-proxy-max">{t('settings.proxy.maxClients')}</Label>
          <Input
            id="radio-proxy-max"
            value={maxClients}
            onChange={(event) => setMaxClients(event.target.value)}
          />
        </div>
      </div>

      <div className="mt-4 rounded-md border border-input bg-muted/20 px-3 py-2 text-xs">
        <div>
          {t('settings.proxy.status')}:{' '}
          {status.listening ? t('settings.proxy.listening') : t('settings.proxy.stopped')}
        </div>
        <div>
          {t('settings.proxy.clients')}: {status.client_count}
        </div>
        <div>
          {t('settings.proxy.drops')}: {status.dropped_messages}/{status.dropped_logs}
        </div>
        {status.model ? (
          <div>
            {t('settings.proxy.model')}: {status.model}
          </div>
        ) : null}
        {status.last_error ? (
          <div className="text-destructive">
            {t('settings.proxy.error')}: {status.last_error}
          </div>
        ) : null}
      </div>

      {loadError ? <p className="mt-2 text-sm text-destructive">{loadError}</p> : null}

      <div className="mt-4">
        <Button type="button" onClick={() => void handleSave()} disabled={busy}>
          {busy ? t('settings.proxy.saving') : t('settings.proxy.save')}
        </Button>
      </div>
    </div>
  );
}
