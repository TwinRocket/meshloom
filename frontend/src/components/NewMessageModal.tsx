import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Dice5 } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Checkbox } from './ui/checkbox';
import { Button } from './ui/button';
import { toast } from './ui/sonner';
import { parseMeshcoreUri } from '../utils/meshcoreUri';
import {
  exactHashtagInput,
  normalizeHashtagInput,
  type HashtagInputResult,
} from '../utils/hashtagInput';

type Tab = 'new-contact' | 'new-channel' | 'hashtag' | 'bulk-hashtag';

interface BulkParseResult {
  channelNames: string[];
  invalidNames: string[];
}

interface NewMessageModalProps {
  open: boolean;
  undecryptedCount: number;
  showBulkAddChannelTab?: boolean;
  prefillRequest?: {
    tab: 'hashtag';
    hashtagName: string;
    nonce: number;
  } | null;
  onClose: () => void;
  onCreateContact: (
    name: string,
    publicKey: string,
    tryHistorical: boolean,
    type?: number
  ) => Promise<void>;
  onCreateChannel: (name: string, key: string, tryHistorical: boolean) => Promise<void>;
  onCreateHashtagChannel: (name: string, tryHistorical: boolean) => Promise<void>;
  onBulkAddHashtagChannels: (channelNames: string[], tryHistorical: boolean) => Promise<void>;
}

function parseHashtagInput(raw: string, permitExtended: boolean): HashtagInputResult {
  return permitExtended ? exactHashtagInput(raw) : normalizeHashtagInput(raw);
}

function parseBulkHashtagNames(rawText: string, permitExtended: boolean): BulkParseResult {
  // When extended names are permitted, whitespace can be part of a name, so split on
  // newlines/commas only. Otherwise keep the whitespace-delimited behavior.
  const tokens = rawText
    .split(permitExtended ? /[\n,]+/ : /[\s,]+/)
    .map((token) => token.trim())
    .filter(Boolean);

  const invalidNames: string[] = [];
  const channelNames: string[] = [];
  const seen = new Set<string>();

  for (const token of tokens) {
    const parsed = parseHashtagInput(token, permitExtended);
    if (!parsed.ok) {
      invalidNames.push(token);
      continue;
    }
    if (seen.has(parsed.name)) {
      continue;
    }
    seen.add(parsed.name);
    channelNames.push(parsed.name);
  }

  return { channelNames, invalidNames };
}

export function NewMessageModal({
  open,
  undecryptedCount,
  showBulkAddChannelTab = false,
  prefillRequest = null,
  onClose,
  onCreateContact,
  onCreateChannel,
  onCreateHashtagChannel,
  onBulkAddHashtagChannels,
}: NewMessageModalProps) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>('new-contact');
  const [name, setName] = useState('');
  const [contactType, setContactType] = useState(1);
  const [contactKey, setContactKey] = useState('');
  const [channelKey, setChannelKey] = useState('');
  const [bulkChannelText, setBulkChannelText] = useState('');
  const [tryHistorical, setTryHistorical] = useState(false);
  const [permitExtended, setPermitExtended] = useState(false);
  const [importUri, setImportUri] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const hashtagInputRef = useRef<HTMLInputElement>(null);
  const bulkTextareaRef = useRef<HTMLTextAreaElement>(null);

  const resetForm = () => {
    setName('');
    setContactType(1);
    setContactKey('');
    setChannelKey('');
    setBulkChannelText('');
    setTryHistorical(false);
    setPermitExtended(false);
    setImportUri('');
    setError('');
  };

  const applyMeshcoreUri = (raw: string): boolean => {
    const parsed = parseMeshcoreUri(raw);
    if (!parsed) {
      return false;
    }
    if (parsed.kind === 'contact') {
      setTab('new-contact');
      setName(parsed.name);
      setContactKey(parsed.publicKey);
      setContactType(parsed.type);
    } else {
      setTab('new-channel');
      setName(parsed.name);
      setChannelKey(parsed.secret);
    }
    setImportUri('');
    setError('');
    return true;
  };

  useEffect(() => {
    if (!open) {
      return;
    }

    if (prefillRequest) {
      setTab(prefillRequest.tab);
      setName(prefillRequest.hashtagName);
      setContactKey('');
      setChannelKey('');
      setBulkChannelText('');
      setTryHistorical(false);
      setPermitExtended(false);
      setImportUri('');
      setError('');
      setLoading(false);
      requestAnimationFrame(() => {
        hashtagInputRef.current?.focus();
      });
      return;
    }

    if (showBulkAddChannelTab) {
      setTab('bulk-hashtag');
      setName('');
      setContactKey('');
      setChannelKey('');
      setBulkChannelText('');
      setTryHistorical(false);
      setPermitExtended(false);
      setImportUri('');
      setError('');
      setLoading(false);
      requestAnimationFrame(() => {
        bulkTextareaRef.current?.focus();
      });
      return;
    }

    setTab('new-contact');
  }, [open, prefillRequest, showBulkAddChannelTab]);

  const handleCreate = async () => {
    setError('');
    setLoading(true);

    try {
      if (tab === 'new-contact') {
        if (!name.trim() || !contactKey.trim()) {
          setError(t('newMessage.nameRequired'));
          return;
        }
        await onCreateContact(name.trim(), contactKey.trim(), tryHistorical, contactType);
      } else if (tab === 'new-channel') {
        if (!name.trim() || !channelKey.trim()) {
          setError(t('newMessage.channelRequired'));
          return;
        }
        await onCreateChannel(name.trim(), channelKey.trim(), tryHistorical);
      } else if (tab === 'hashtag') {
        const parsed = parseHashtagInput(name, permitExtended);
        if (!parsed.ok) {
          setError(t(parsed.error));
          return;
        }
        await onCreateHashtagChannel(parsed.name, tryHistorical);
      } else {
        const { channelNames, invalidNames } = parseBulkHashtagNames(
          bulkChannelText,
          permitExtended
        );
        if (channelNames.length === 0) {
          setError(t('newMessage.bulkEmpty'));
          return;
        }
        if (invalidNames.length > 0) {
          setError(t('newMessage.invalidNames', { names: invalidNames.join(', ') }));
          return;
        }
        await onBulkAddHashtagChannels(channelNames, tryHistorical);
      }

      resetForm();
      onClose();
    } catch (err) {
      toast.error(t('newMessage.createFailed'), {
        description: err instanceof Error ? err.message : undefined,
      });
      setError(err instanceof Error ? err.message : t('newMessage.createFailedShort'));
    } finally {
      setLoading(false);
    }
  };

  const handleCreateAndAddAnother = async () => {
    setError('');
    const parsed = parseHashtagInput(name, permitExtended);
    if (!parsed.ok) {
      setError(t(parsed.error));
      return;
    }

    setLoading(true);
    try {
      await onCreateHashtagChannel(parsed.name, tryHistorical);
      setName('');
      hashtagInputRef.current?.focus();
    } catch (err) {
      toast.error(t('newMessage.createFailed'), {
        description: err instanceof Error ? err.message : undefined,
      });
      setError(err instanceof Error ? err.message : t('newMessage.createFailedShort'));
    } finally {
      setLoading(false);
    }
  };

  const showHistoricalOption = undecryptedCount > 0;

  return (
    <Dialog
      open={open}
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          resetForm();
          onClose();
        }
      }}
    >
      <DialogContent className="sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>{t('newMessage.title')}</DialogTitle>
          <DialogDescription className="sr-only">
            {tab === 'new-contact' && t('newMessage.descContact')}
            {tab === 'new-channel' && t('newMessage.descChannel')}
            {tab === 'hashtag' && t('newMessage.descHashtag')}
            {tab === 'bulk-hashtag' && t('newMessage.descBulk')}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          <Label htmlFor="meshcore-uri">{t('share.importLabel')}</Label>
          <Input
            id="meshcore-uri"
            value={importUri}
            onChange={(e) => {
              const value = e.target.value;
              setImportUri(value);
              if (applyMeshcoreUri(value)) {
                return;
              }
              if (value.trim().toLowerCase().startsWith('meshcore:')) {
                setError(t('share.invalidUri'));
              }
            }}
            placeholder={t('share.importPlaceholder')}
          />
        </div>

        <Tabs
          value={tab}
          onValueChange={(value) => {
            setTab(value as Tab);
            resetForm();
          }}
          className="w-full"
        >
          <TabsList
            className={
              showBulkAddChannelTab ? 'grid w-full grid-cols-4' : 'grid w-full grid-cols-3'
            }
          >
            <TabsTrigger value="new-contact">{t('newMessage.tabContact')}</TabsTrigger>
            <TabsTrigger value="new-channel">{t('newMessage.tabChannel')}</TabsTrigger>
            <TabsTrigger value="hashtag">{t('newMessage.tabHashtag')}</TabsTrigger>
            {showBulkAddChannelTab && (
              <TabsTrigger value="bulk-hashtag">{t('newMessage.tabBulk')}</TabsTrigger>
            )}
          </TabsList>

          <TabsContent value="new-contact" className="mt-4 space-y-4">
            <div className="space-y-2">
              <Label htmlFor="contact-name">{t('newMessage.name')}</Label>
              <Input
                id="contact-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                onPaste={(e) => {
                  const text = e.clipboardData.getData('text');
                  if (applyMeshcoreUri(text)) {
                    e.preventDefault();
                  }
                }}
                placeholder={t('newMessage.namePlaceholder')}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="contact-key">{t('newMessage.publicKey')}</Label>
              <Input
                id="contact-key"
                value={contactKey}
                onChange={(e) => setContactKey(e.target.value)}
                onPaste={(e) => {
                  const text = e.clipboardData.getData('text');
                  if (applyMeshcoreUri(text)) {
                    e.preventDefault();
                  }
                }}
                placeholder={t('newMessage.publicKeyPlaceholder')}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="contact-type">{t('newMessage.type')}</Label>
              <select
                id="contact-type"
                value={contactType}
                onChange={(e) => setContactType(Number(e.target.value))}
                className="block h-9 w-full rounded-md border border-input bg-background px-3 text-sm shadow-sm"
              >
                <option value={1}>{t('newMessage.typeClient')}</option>
                <option value={2}>{t('newMessage.typeRepeater')}</option>
                <option value={3}>{t('newMessage.typeRoom')}</option>
                <option value={4}>{t('newMessage.typeSensor')}</option>
              </select>
            </div>
          </TabsContent>

          <TabsContent value="new-channel" className="mt-4 space-y-4">
            <div className="space-y-2">
              <Label htmlFor="channel-name">{t('newMessage.channelName')}</Label>
              <Input
                id="channel-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                onPaste={(e) => {
                  const text = e.clipboardData.getData('text');
                  if (applyMeshcoreUri(text)) {
                    e.preventDefault();
                  }
                }}
                placeholder={t('newMessage.channelNamePlaceholder')}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="channel-key">{t('newMessage.channelKey')}</Label>
              <div className="flex gap-2">
                <Input
                  id="channel-key"
                  value={channelKey}
                  onChange={(e) => setChannelKey(e.target.value)}
                  onPaste={(e) => {
                    const text = e.clipboardData.getData('text');
                    if (applyMeshcoreUri(text)) {
                      e.preventDefault();
                    }
                  }}
                  placeholder={t('newMessage.channelKeyPlaceholder')}
                  className="flex-1"
                />
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  onClick={() => {
                    const bytes = new Uint8Array(16);
                    crypto.getRandomValues(bytes);
                    const hex = Array.from(bytes)
                      .map((byte) => byte.toString(16).padStart(2, '0'))
                      .join('');
                    setChannelKey(hex);
                  }}
                  title={t('newMessage.generateKey')}
                  aria-label={t('newMessage.generateKey')}
                >
                  <Dice5 className="h-4 w-4" aria-hidden="true" />
                </Button>
              </div>
            </div>
          </TabsContent>

          <TabsContent value="hashtag" className="mt-4">
            <div className="space-y-2">
              <Label htmlFor="hashtag-name">{t('newMessage.hashtagLabel')}</Label>
              <div className="flex items-center gap-1">
                <span className="text-sm text-muted-foreground">#</span>
                <Input
                  ref={hashtagInputRef}
                  id="hashtag-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={t('newMessage.hashtagPlaceholder')}
                  className="flex-1"
                />
              </div>
            </div>
            <div className="mt-3 space-y-1">
              <label className="flex cursor-pointer items-center gap-3">
                <input
                  type="checkbox"
                  checked={permitExtended}
                  onChange={(e) => setPermitExtended(e.target.checked)}
                  className="h-4 w-4 rounded border-input accent-primary"
                />
                <span className="text-sm">{t('newMessage.permitExtended')}</span>
              </label>
              <p className="pl-7 text-xs text-muted-foreground">
                {t('newMessage.permitExtendedHelp')}
              </p>
            </div>
          </TabsContent>

          {showBulkAddChannelTab && (
            <TabsContent value="bulk-hashtag" className="mt-4 space-y-4">
              <div className="space-y-2">
                <Label htmlFor="bulk-hashtag-names">{t('newMessage.bulkLabel')}</Label>
                <textarea
                  ref={bulkTextareaRef}
                  id="bulk-hashtag-names"
                  aria-label={t('newMessage.bulkNamesAria')}
                  value={bulkChannelText}
                  onChange={(e) => setBulkChannelText(e.target.value)}
                  placeholder={'#ops\nmesh-chat\nanother-channel'}
                  className="min-h-48 w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm outline-none transition-colors placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
                />
                <p className="text-xs text-muted-foreground">
                  {permitExtended ? t('newMessage.bulkHelpExtended') : t('newMessage.bulkHelp')}
                </p>
              </div>
              <div className="space-y-1">
                <label className="flex cursor-pointer items-center gap-3">
                  <input
                    type="checkbox"
                    checked={permitExtended}
                    onChange={(e) => setPermitExtended(e.target.checked)}
                    className="h-4 w-4 rounded border-input accent-primary"
                  />
                  <span className="text-sm">{t('newMessage.permitExtended')}</span>
                </label>
                <p className="pl-7 text-xs text-muted-foreground">
                  {t('newMessage.permitExtendedHelpBulk')}
                </p>
              </div>
            </TabsContent>
          )}
        </Tabs>

        {showHistoricalOption && (
          <div className="space-y-1">
            <div className="flex items-center justify-end space-x-2">
              <Label
                htmlFor="try-historical"
                className="cursor-pointer text-sm text-muted-foreground"
              >
                {t('newMessage.tryDecrypt', { count: undecryptedCount })}
              </Label>
              <Checkbox
                id="try-historical"
                checked={tryHistorical}
                onCheckedChange={(checked) => setTryHistorical(checked === true)}
              />
            </div>
            {tryHistorical && (
              <p className="text-right text-xs text-muted-foreground">
                {t('newMessage.tryDecryptHelp')}
              </p>
            )}
          </div>
        )}

        {error && (
          <div className="text-sm text-destructive" role="alert">
            {error}
          </div>
        )}

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => {
              resetForm();
              onClose();
            }}
          >
            {t('newMessage.cancel')}
          </Button>
          {tab === 'hashtag' && (
            <Button variant="secondary" onClick={handleCreateAndAddAnother} disabled={loading}>
              {loading ? t('newMessage.creating') : t('newMessage.createAnother')}
            </Button>
          )}
          <Button onClick={handleCreate} disabled={loading}>
            {loading
              ? tab === 'bulk-hashtag'
                ? t('newMessage.adding')
                : t('newMessage.creating')
              : tab === 'bulk-hashtag'
                ? t('newMessage.addChannels')
                : t('newMessage.create')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
