import { useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Check,
  CheckCircle2,
  Edit2,
  Loader2,
  Play,
  Plus,
  Radio as RadioIcon,
  RefreshCw,
  Square,
  Trash2,
  XCircle,
} from 'lucide-react';
import { api, formatApiError } from '../../api';
import { useRadioContext } from '../../contexts/RadioContext';
import { cn } from '../../lib/utils';
import type { RadioCreate, RadioRecord, RadioTransportKind, RadioUpdate } from '../../types';
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
import { toast } from '../ui/sonner';
import { getRadioStatusDotClass } from '../RadioSelector';

interface RadioFormData {
  name: string;
  transport: RadioTransportKind;
  tcp_host: string;
  tcp_port: number;
  serial_port: string;
  serial_baudrate: number;
  ble_address: string;
  ble_pin: string;
  enabled: boolean;
  auto_connect: boolean;
}

const DEFAULT_FORM: RadioFormData = {
  name: '',
  transport: 'tcp',
  tcp_host: '127.0.0.1',
  tcp_port: 5000,
  serial_port: '',
  serial_baudrate: 115200,
  ble_address: '',
  ble_pin: '',
  enabled: true,
  auto_connect: true,
};

export function SettingsRadiosManagement({ className }: { className?: string }) {
  const { t } = useTranslation();
  const { radios, activeRadioId, setActiveRadioId, refreshRadios } = useRadioContext();

  const [addModalOpen, setAddModalOpen] = useState(false);
  const [editRadio, setEditRadio] = useState<RadioRecord | null>(null);
  const [deleteRadio, setDeleteRadio] = useState<RadioRecord | null>(null);
  const [purgeData, setPurgeData] = useState(false);

  const [formData, setFormData] = useState<RadioFormData>(DEFAULT_FORM);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);

  const [actionBusyId, setActionBusyId] = useState<string | null>(null);

  const handleOpenAddModal = () => {
    setFormData(DEFAULT_FORM);
    setTestResult(null);
    setAddModalOpen(true);
  };

  const handleOpenEditModal = (radio: RadioRecord) => {
    setFormData({
      name: radio.name,
      transport: radio.transport || 'tcp',
      tcp_host: radio.tcp_host || '127.0.0.1',
      tcp_port: radio.tcp_port ?? 5000,
      serial_port: radio.serial_port || '',
      serial_baudrate: radio.serial_baudrate || 115200,
      ble_address: radio.ble_address || '',
      ble_pin: '',
      enabled: radio.enabled,
      auto_connect: radio.auto_connect,
    });
    setTestResult(null);
    setEditRadio(radio);
  };

  const handleTestTransport = async () => {
    setIsTesting(true);
    setTestResult(null);
    try {
      const payload: RadioCreate = {
        name: formData.name || 'Test Radio',
        transport: formData.transport,
        tcp_host: formData.tcp_host,
        tcp_port: Number(formData.tcp_port),
        serial_port: formData.serial_port,
        serial_baudrate: Number(formData.serial_baudrate),
        ble_address: formData.ble_address,
        ble_pin: formData.ble_pin,
      };
      const res = await api.testRadioTransport(payload);
      setTestResult(res);
      if (res.success) {
        toast.success(res.message);
      } else {
        toast.error(res.message);
      }
    } catch (err) {
      const msg = formatApiError(err, t);
      setTestResult({ success: false, message: msg });
      toast.error(msg);
    } finally {
      setIsTesting(false);
    }
  };

  const handleSaveAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.name.trim()) {
      toast.error(t('settings.radios.nameRequired', 'Radio name is required'));
      return;
    }

    setIsSubmitting(true);
    try {
      const payload: RadioCreate = {
        name: formData.name.trim(),
        transport: formData.transport,
        tcp_host: formData.tcp_host.trim(),
        tcp_port: Number(formData.tcp_port),
        serial_port: formData.serial_port.trim(),
        serial_baudrate: Number(formData.serial_baudrate),
        ble_address: formData.ble_address.trim(),
        ble_pin: formData.ble_pin.trim(),
        enabled: formData.enabled,
        auto_connect: formData.auto_connect,
      };
      await api.createRadio(payload);
      toast.success(t('settings.radios.createdSuccess', 'Radio created successfully'));
      setAddModalOpen(false);
      await refreshRadios();
    } catch (err) {
      toast.error(formatApiError(err, t));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleSaveEdit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editRadio) return;
    if (!formData.name.trim()) {
      toast.error(t('settings.radios.nameRequired', 'Radio name is required'));
      return;
    }

    setIsSubmitting(true);
    try {
      const patch: RadioUpdate = {
        name: formData.name.trim(),
        transport: formData.transport,
        tcp_host: formData.tcp_host.trim(),
        tcp_port: Number(formData.tcp_port),
        serial_port: formData.serial_port.trim(),
        serial_baudrate: Number(formData.serial_baudrate),
        ble_address: formData.ble_address.trim(),
        ble_pin: formData.ble_pin.trim(),
        enabled: formData.enabled,
        auto_connect: formData.auto_connect,
      };
      await api.updateRadio(editRadio.id, patch);
      toast.success(t('settings.radios.updatedSuccess', 'Radio updated successfully'));
      setEditRadio(null);
      await refreshRadios();
    } catch (err) {
      toast.error(formatApiError(err, t));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleConfirmDelete = async () => {
    if (!deleteRadio) return;
    if (deleteRadio.id === 'default') {
      toast.error(t('settings.radios.cannotDeleteDefault', 'Default radio cannot be deleted'));
      return;
    }

    setIsSubmitting(true);
    try {
      await api.deleteRadio(deleteRadio.id, purgeData);
      toast.success(t('settings.radios.deletedSuccess', 'Radio deleted successfully'));
      setDeleteRadio(null);
      await refreshRadios();
    } catch (err) {
      toast.error(formatApiError(err, t));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleConnectRadio = useCallback(
    async (radioId: string) => {
      setActionBusyId(radioId);
      try {
        await api.connectRadio(radioId);
        toast.success(t('settings.radios.connectedSuccess', 'Radio connected'));
        await refreshRadios();
      } catch (err) {
        toast.error(formatApiError(err, t));
      } finally {
        setActionBusyId(null);
      }
    },
    [refreshRadios, t]
  );

  const handleDisconnectRadio = useCallback(
    async (radioId: string) => {
      setActionBusyId(radioId);
      try {
        await api.disconnectRadio(radioId);
        toast.success(t('settings.radios.disconnectedSuccess', 'Radio disconnected'));
        await refreshRadios();
      } catch (err) {
        toast.error(formatApiError(err, t));
      } finally {
        setActionBusyId(null);
      }
    },
    [refreshRadios, t]
  );

  const handleReconnectRadio = useCallback(
    async (radioId: string) => {
      setActionBusyId(radioId);
      try {
        await api.reconnectRadio(radioId);
        toast.success(t('settings.radios.reconnectedSuccess', 'Radio reconnected'));
        await refreshRadios();
      } catch (err) {
        toast.error(formatApiError(err, t));
      } finally {
        setActionBusyId(null);
      }
    },
    [refreshRadios, t]
  );

  const renderTransportSummary = (radio: RadioRecord) => {
    if (!radio.transport) {
      return (
        <span className="text-muted-foreground">
          {t('radioStatus.notConfigured', 'Not configured')}
        </span>
      );
    }
    if (radio.transport === 'tcp') {
      return (
        <span>
          TCP · {radio.tcp_host}:{radio.tcp_port ?? 5000}
        </span>
      );
    }
    if (radio.transport === 'serial') {
      return (
        <span>
          Serial · {radio.serial_port || 'Auto'} @ {radio.serial_baudrate || 115200}
        </span>
      );
    }
    if (radio.transport === 'ble') {
      return <span>BLE · {radio.ble_address}</span>;
    }
    return <span>{radio.transport}</span>;
  };

  return (
    <div className={cn('space-y-6', className)}>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 className="text-base font-semibold tracking-tight">
            {t('settings.radios.managementTitle', 'Configured Radios')}
          </h3>
          <p className="text-[0.8125rem] text-muted-foreground">
            {t(
              'settings.radios.managementHelp',
              'Manage multiple MeshCore radio instances. Switch active radios or manage connections.'
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void refreshRadios()}
            className="gap-1.5"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            <span>{t('common.refresh', 'Refresh')}</span>
          </Button>
          <Button type="button" size="sm" onClick={handleOpenAddModal} className="gap-1.5">
            <Plus className="h-3.5 w-3.5" />
            <span>{t('settings.radios.addRadio', 'Add Radio')}</span>
          </Button>
        </div>
      </div>

      <div className="space-y-3">
        {radios.map((radio) => {
          const isActive = radio.id === activeRadioId;
          const isBusy = actionBusyId === radio.id;
          const dotClass = getRadioStatusDotClass(radio);

          return (
            <div
              key={radio.id}
              className={cn(
                'flex flex-col gap-3 rounded-xl border p-4 transition-colors sm:flex-row sm:items-center sm:justify-between',
                isActive
                  ? 'border-primary/50 bg-primary/5'
                  : 'border-border/80 bg-card hover:border-border'
              )}
            >
              <div className="flex items-start gap-3">
                <span
                  className={cn('mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full', dotClass)}
                  aria-hidden="true"
                />
                <div className="min-w-0 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold text-foreground text-sm">
                      {radio.name || radio.id}
                    </span>
                    {radio.id === 'default' && (
                      <span className="rounded bg-muted px-1.5 py-0.5 text-[0.625rem] font-medium uppercase tracking-wider text-muted-foreground">
                        {t('settings.radios.primaryBadge', 'Primary')}
                      </span>
                    )}
                    {isActive ? (
                      <span className="inline-flex items-center gap-1 rounded bg-primary/10 px-1.5 py-0.5 text-[0.625rem] font-semibold uppercase tracking-wider text-primary">
                        <Check className="h-3 w-3" />
                        {t('settings.radios.activeBadge', 'Active')}
                      </span>
                    ) : (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => setActiveRadioId(radio.id)}
                        className="h-5 px-1.5 text-[0.6875rem] text-muted-foreground hover:text-foreground"
                      >
                        {t('settings.radios.makeActive', 'Make Active')}
                      </Button>
                    )}
                  </div>

                  <div className="text-xs text-muted-foreground">
                    {renderTransportSummary(radio)}
                  </div>

                  {radio.connection_info && (
                    <div className="text-[0.6875rem] text-muted-foreground">
                      {radio.connection_info}
                    </div>
                  )}

                  {radio.last_error && !radio.is_connected && (
                    <div className="text-[0.6875rem] text-destructive">{radio.last_error}</div>
                  )}
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-1.5 sm:self-center">
                {radio.is_connected ? (
                  <>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={isBusy}
                      onClick={() => handleDisconnectRadio(radio.id)}
                      className="h-8 gap-1 border-rose-500/30 text-rose-600 hover:bg-rose-500/10 dark:text-rose-400"
                    >
                      <Square className="h-3 w-3" />
                      <span>{t('settings.radios.disconnect', 'Disconnect')}</span>
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={isBusy}
                      onClick={() => handleReconnectRadio(radio.id)}
                      className="h-8 gap-1"
                    >
                      <RefreshCw className={cn('h-3 w-3', isBusy && 'animate-spin')} />
                      <span>{t('settings.radios.reconnect', 'Reconnect')}</span>
                    </Button>
                  </>
                ) : (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={isBusy || !radio.enabled}
                    onClick={() => handleConnectRadio(radio.id)}
                    className="h-8 gap-1 border-emerald-500/30 text-emerald-600 hover:bg-emerald-500/10 dark:text-emerald-400"
                  >
                    <Play className="h-3 w-3" />
                    <span>{t('settings.radios.connect', 'Connect')}</span>
                  </Button>
                )}

                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => handleOpenEditModal(radio)}
                  className="h-8 gap-1"
                >
                  <Edit2 className="h-3 w-3" />
                  <span>{t('common.edit', 'Edit')}</span>
                </Button>

                {radio.id !== 'default' && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setPurgeData(false);
                      setDeleteRadio(radio);
                    }}
                    className="h-8 gap-1 border-destructive/40 text-destructive hover:bg-destructive/10"
                  >
                    <Trash2 className="h-3 w-3" />
                    <span>{t('common.delete', 'Delete')}</span>
                  </Button>
                )}
              </div>
            </div>
          );
        })}

        {radios.length === 0 && (
          <div className="rounded-xl border border-dashed border-border/80 p-8 text-center text-muted-foreground">
            <RadioIcon className="mx-auto mb-2 h-8 w-8 opacity-40" />
            <p className="text-sm">{t('settings.radios.noRadios', 'No radios configured.')}</p>
          </div>
        )}
      </div>

      {/* ── Add Radio Dialog ── */}
      <Dialog open={addModalOpen} onOpenChange={setAddModalOpen}>
        <DialogContent className="max-w-md">
          <form onSubmit={handleSaveAdd}>
            <DialogHeader>
              <DialogTitle>{t('settings.radios.addRadio', 'Add Radio')}</DialogTitle>
              <DialogDescription>
                {t(
                  'settings.radios.addRadioDesc',
                  'Configure connection parameters for a new MeshCore radio instance.'
                )}
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-4 py-4">
              <div className="space-y-1.5">
                <Label htmlFor="add-radio-name">{t('settings.radio.radioName', 'Name')}</Label>
                <Input
                  id="add-radio-name"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                  placeholder="e.g. Roof Repeater"
                  required
                />
              </div>

              <div className="space-y-1.5">
                <Label>{t('settings.radio.transportMode', 'Transport')}</Label>
                <div className="grid grid-cols-3 gap-2">
                  {(['tcp', 'serial', 'ble'] as RadioTransportKind[]).map((kind) => (
                    <Button
                      key={kind}
                      type="button"
                      variant={formData.transport === kind ? 'default' : 'outline'}
                      size="sm"
                      onClick={() => setFormData({ ...formData, transport: kind })}
                      className="uppercase"
                    >
                      {kind}
                    </Button>
                  ))}
                </div>
              </div>

              {formData.transport === 'tcp' && (
                <div className="grid grid-cols-3 gap-2">
                  <div className="col-span-2 space-y-1.5">
                    <Label htmlFor="add-tcp-host">TCP Host</Label>
                    <Input
                      id="add-tcp-host"
                      value={formData.tcp_host}
                      onChange={(e) => setFormData({ ...formData, tcp_host: e.target.value })}
                      placeholder="127.0.0.1"
                      required
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="add-tcp-port">Port</Label>
                    <Input
                      id="add-tcp-port"
                      type="number"
                      value={formData.tcp_port}
                      onChange={(e) =>
                        setFormData({ ...formData, tcp_port: Number(e.target.value) })
                      }
                      placeholder="5000"
                      required
                    />
                  </div>
                </div>
              )}

              {formData.transport === 'serial' && (
                <div className="grid grid-cols-3 gap-2">
                  <div className="col-span-2 space-y-1.5">
                    <Label htmlFor="add-serial-port">Serial Port</Label>
                    <Input
                      id="add-serial-port"
                      value={formData.serial_port}
                      onChange={(e) => setFormData({ ...formData, serial_port: e.target.value })}
                      placeholder="/dev/ttyUSB0 or COM3"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="add-serial-baud">Baudrate</Label>
                    <Input
                      id="add-serial-baud"
                      type="number"
                      value={formData.serial_baudrate}
                      onChange={(e) =>
                        setFormData({ ...formData, serial_baudrate: Number(e.target.value) })
                      }
                      placeholder="115200"
                      required
                    />
                  </div>
                </div>
              )}

              {formData.transport === 'ble' && (
                <div className="grid grid-cols-3 gap-2">
                  <div className="col-span-2 space-y-1.5">
                    <Label htmlFor="add-ble-addr">BLE Address</Label>
                    <Input
                      id="add-ble-addr"
                      value={formData.ble_address}
                      onChange={(e) => setFormData({ ...formData, ble_address: e.target.value })}
                      placeholder="AA:BB:CC:DD:EE:FF"
                      required
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="add-ble-pin">PIN</Label>
                    <Input
                      id="add-ble-pin"
                      value={formData.ble_pin}
                      onChange={(e) => setFormData({ ...formData, ble_pin: e.target.value })}
                      placeholder="123456"
                    />
                  </div>
                </div>
              )}

              <div className="flex flex-col gap-3 pt-2">
                <div className="flex items-center gap-2">
                  <Checkbox
                    id="add-enabled"
                    checked={formData.enabled}
                    onCheckedChange={(checked) =>
                      setFormData({ ...formData, enabled: checked === true })
                    }
                  />
                  <Label htmlFor="add-enabled" className="text-sm font-normal">
                    {t('settings.radios.enabled', 'Enable this radio')}
                  </Label>
                </div>

                <div className="flex items-center gap-2">
                  <Checkbox
                    id="add-auto-connect"
                    checked={formData.auto_connect}
                    onCheckedChange={(checked) =>
                      setFormData({ ...formData, auto_connect: checked === true })
                    }
                  />
                  <Label htmlFor="add-auto-connect" className="text-sm font-normal">
                    {t('settings.radios.autoConnect', 'Auto-connect on startup / reconnection')}
                  </Label>
                </div>
              </div>

              {testResult && (
                <div
                  className={cn(
                    'flex items-center gap-2 rounded-lg p-3 text-xs',
                    testResult.success
                      ? 'border border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                      : 'border border-destructive/30 bg-destructive/10 text-destructive'
                  )}
                >
                  {testResult.success ? (
                    <CheckCircle2 className="h-4 w-4 shrink-0" />
                  ) : (
                    <XCircle className="h-4 w-4 shrink-0" />
                  )}
                  <span className="break-words">{testResult.message}</span>
                </div>
              )}
            </div>

            <DialogFooter className="gap-2 sm:gap-0">
              <Button
                type="button"
                variant="outline"
                disabled={isTesting || isSubmitting}
                onClick={handleTestTransport}
                className="gap-1.5"
              >
                {isTesting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                <span>{t('settings.radios.testConnection', 'Test Connection')}</span>
              </Button>
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
                <span>{t('common.save', 'Save')}</span>
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* ── Edit Radio Dialog ── */}
      <Dialog open={editRadio !== null} onOpenChange={(open) => !open && setEditRadio(null)}>
        <DialogContent className="max-w-md">
          <form onSubmit={handleSaveEdit}>
            <DialogHeader>
              <DialogTitle>{t('settings.radios.editRadio', 'Edit Radio')}</DialogTitle>
              <DialogDescription>
                {t(
                  'settings.radios.editRadioDesc',
                  'Update configuration parameters for this radio instance.'
                )}
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-4 py-4">
              <div className="space-y-1.5">
                <Label htmlFor="edit-radio-name">{t('settings.radio.radioName', 'Name')}</Label>
                <Input
                  id="edit-radio-name"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                  required
                />
              </div>

              <div className="space-y-1.5">
                <Label>{t('settings.radio.transportMode', 'Transport')}</Label>
                <div className="grid grid-cols-3 gap-2">
                  {(['tcp', 'serial', 'ble'] as RadioTransportKind[]).map((kind) => (
                    <Button
                      key={kind}
                      type="button"
                      variant={formData.transport === kind ? 'default' : 'outline'}
                      size="sm"
                      onClick={() => setFormData({ ...formData, transport: kind })}
                      className="uppercase"
                    >
                      {kind}
                    </Button>
                  ))}
                </div>
              </div>

              {formData.transport === 'tcp' && (
                <div className="grid grid-cols-3 gap-2">
                  <div className="col-span-2 space-y-1.5">
                    <Label htmlFor="edit-tcp-host">TCP Host</Label>
                    <Input
                      id="edit-tcp-host"
                      value={formData.tcp_host}
                      onChange={(e) => setFormData({ ...formData, tcp_host: e.target.value })}
                      required
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="edit-tcp-port">Port</Label>
                    <Input
                      id="edit-tcp-port"
                      type="number"
                      value={formData.tcp_port}
                      onChange={(e) =>
                        setFormData({ ...formData, tcp_port: Number(e.target.value) })
                      }
                      required
                    />
                  </div>
                </div>
              )}

              {formData.transport === 'serial' && (
                <div className="grid grid-cols-3 gap-2">
                  <div className="col-span-2 space-y-1.5">
                    <Label htmlFor="edit-serial-port">Serial Port</Label>
                    <Input
                      id="edit-serial-port"
                      value={formData.serial_port}
                      onChange={(e) => setFormData({ ...formData, serial_port: e.target.value })}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="edit-serial-baud">Baudrate</Label>
                    <Input
                      id="edit-serial-baud"
                      type="number"
                      value={formData.serial_baudrate}
                      onChange={(e) =>
                        setFormData({ ...formData, serial_baudrate: Number(e.target.value) })
                      }
                      required
                    />
                  </div>
                </div>
              )}

              {formData.transport === 'ble' && (
                <div className="grid grid-cols-3 gap-2">
                  <div className="col-span-2 space-y-1.5">
                    <Label htmlFor="edit-ble-addr">BLE Address</Label>
                    <Input
                      id="edit-ble-addr"
                      value={formData.ble_address}
                      onChange={(e) => setFormData({ ...formData, ble_address: e.target.value })}
                      required
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="edit-ble-pin">PIN</Label>
                    <Input
                      id="edit-ble-pin"
                      value={formData.ble_pin}
                      onChange={(e) => setFormData({ ...formData, ble_pin: e.target.value })}
                      placeholder="Leave blank to keep existing"
                    />
                  </div>
                </div>
              )}

              <div className="flex flex-col gap-3 pt-2">
                <div className="flex items-center gap-2">
                  <Checkbox
                    id="edit-enabled"
                    checked={formData.enabled}
                    onCheckedChange={(checked) =>
                      setFormData({ ...formData, enabled: checked === true })
                    }
                  />
                  <Label htmlFor="edit-enabled" className="text-sm font-normal">
                    {t('settings.radios.enabled', 'Enable this radio')}
                  </Label>
                </div>

                <div className="flex items-center gap-2">
                  <Checkbox
                    id="edit-auto-connect"
                    checked={formData.auto_connect}
                    onCheckedChange={(checked) =>
                      setFormData({ ...formData, auto_connect: checked === true })
                    }
                  />
                  <Label htmlFor="edit-auto-connect" className="text-sm font-normal">
                    {t('settings.radios.autoConnect', 'Auto-connect on startup / reconnection')}
                  </Label>
                </div>
              </div>

              {testResult && (
                <div
                  className={cn(
                    'flex items-center gap-2 rounded-lg p-3 text-xs',
                    testResult.success
                      ? 'border border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                      : 'border border-destructive/30 bg-destructive/10 text-destructive'
                  )}
                >
                  {testResult.success ? (
                    <CheckCircle2 className="h-4 w-4 shrink-0" />
                  ) : (
                    <XCircle className="h-4 w-4 shrink-0" />
                  )}
                  <span className="break-words">{testResult.message}</span>
                </div>
              )}
            </div>

            <DialogFooter className="gap-2 sm:gap-0">
              <Button
                type="button"
                variant="outline"
                disabled={isTesting || isSubmitting}
                onClick={handleTestTransport}
                className="gap-1.5"
              >
                {isTesting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                <span>{t('settings.radios.testConnection', 'Test Connection')}</span>
              </Button>
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
                <span>{t('common.save', 'Save')}</span>
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* ── Delete Confirmation Dialog ── */}
      <Dialog open={deleteRadio !== null} onOpenChange={(open) => !open && setDeleteRadio(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="text-destructive">
              {t('settings.radios.deleteTitle', 'Delete Radio')}
            </DialogTitle>
            <DialogDescription>
              {t(
                'settings.radios.deleteConfirm',
                'Are you sure you want to delete radio "{{name}}"? This action cannot be undone.',
                { name: deleteRadio?.name || deleteRadio?.id }
              )}
            </DialogDescription>
          </DialogHeader>

          <div className="py-2">
            <div className="flex items-center gap-2">
              <Checkbox
                id="delete-purge"
                checked={purgeData}
                onCheckedChange={(checked) => setPurgeData(checked === true)}
              />
              <Label htmlFor="delete-purge" className="text-sm font-normal text-muted-foreground">
                {t(
                  'settings.radios.purgeDataLabel',
                  'Purge stored data for this radio (contacts, messages, channels, packets)'
                )}
              </Label>
            </div>
          </div>

          <DialogFooter className="gap-2 sm:gap-0">
            <Button
              type="button"
              variant="outline"
              disabled={isSubmitting}
              onClick={() => setDeleteRadio(null)}
            >
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={isSubmitting}
              onClick={handleConfirmDelete}
            >
              {isSubmitting && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
              <span>{t('common.delete', 'Delete')}</span>
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
