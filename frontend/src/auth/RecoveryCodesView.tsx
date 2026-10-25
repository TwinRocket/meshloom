import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Copy, Download } from 'lucide-react';

import { Button } from '../components/ui/button';
import { Checkbox } from '../components/ui/checkbox';
import { Label } from '../components/ui/label';
import { toast } from '../components/ui/sonner';

/**
 * The recovery codes, once: the server keeps only hashes. The reader copies or downloads
 * them and has to say they are kept before the codes disappear from the page.
 */
export function RecoveryCodesView({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const { t } = useTranslation();
  const [saved, setSaved] = useState(false);
  // `navigator.clipboard` is absent over plain HTTP on a LAN: the list stays selectable.
  const canCopy = typeof navigator !== 'undefined' && !!navigator.clipboard;
  const text = codes.join('\n');

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(t('auth.recoveryCodes.copied'));
    } catch {
      toast.error(t('auth.recoveryCodes.copyFailed'));
    }
  };

  const download = () => {
    const blob = new Blob([`${t('auth.recoveryCodes.fileHeader')}\n\n${text}\n`], {
      type: 'text/plain',
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'meshloom-recovery-codes.txt';
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-3">
      <h4 className="text-sm font-medium">{t('auth.recoveryCodes.title')}</h4>
      <p
        role="alert"
        className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3 text-sm font-medium"
      >
        {t('auth.recoveryCodes.warning')}
      </p>
      <ul
        aria-label={t('auth.recoveryCodes.title')}
        className="grid grid-cols-1 gap-1 rounded-md border border-border bg-muted/40 p-3 font-mono text-sm min-[420px]:grid-cols-2"
      >
        {codes.map((code) => (
          <li key={code} className="select-all break-all">
            {code}
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap gap-2">
        {canCopy && (
          <Button type="button" variant="outline" size="sm" onClick={() => void copy()}>
            <Copy className="mr-1.5 h-4 w-4" aria-hidden="true" />
            {t('auth.recoveryCodes.copy')}
          </Button>
        )}
        <Button type="button" variant="outline" size="sm" onClick={download}>
          <Download className="mr-1.5 h-4 w-4" aria-hidden="true" />
          {t('auth.recoveryCodes.download')}
        </Button>
      </div>
      <div className="flex items-start gap-2">
        <Checkbox
          id="recovery-codes-saved"
          className="mt-0.5"
          checked={saved}
          onCheckedChange={(checked) => setSaved(checked === true)}
        />
        <Label htmlFor="recovery-codes-saved">{t('auth.recoveryCodes.saved')}</Label>
      </div>
      <Button type="button" disabled={!saved} onClick={onDone}>
        {t('auth.recoveryCodes.done')}
      </Button>
    </div>
  );
}
