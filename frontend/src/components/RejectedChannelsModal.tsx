import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../api';
import type { RejectedChannel } from '../types';
import { Button } from './ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog';
import { toast } from './ui/sonner';

interface Props {
  open: boolean;
  onClose: () => void;
  onAdopted: (key: string, name: string) => void;
}

export function RejectedChannelsModal({ open, onClose, onAdopted }: Props) {
  const { t } = useTranslation();
  const [items, setItems] = useState<RejectedChannel[]>([]);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    api
      .getRejectedChannels()
      .then((rows) => {
        if (!cancelled) setItems(rows);
      })
      .catch((err) => {
        console.error('Failed to load refused channels:', err);
        toast.error(t('discovered.rejectedLoadFailed'), {
          description: formatApiError(err, t),
        });
      });
    return () => {
      cancelled = true;
    };
  }, [open, t]);

  const handleAdopt = async (item: RejectedChannel) => {
    setBusyKey(item.key);
    try {
      const stored = await api.adoptChannel(item.key);
      setItems((prev) => prev.filter((row) => row.key !== item.key));
      onAdopted(stored.key, stored.name);
    } catch (err) {
      console.error('Failed to adopt refused channel:', err);
      toast.error(t('discovered.adoptFailed'), {
        description: formatApiError(err, t),
      });
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t('discovered.rejectedTitle')}</DialogTitle>
        </DialogHeader>
        {items.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('discovered.rejectedEmpty')}</p>
        ) : (
          <ul className="max-h-[60vh] divide-y divide-border overflow-y-auto">
            {items.map((item) => (
              <li key={item.key} className="flex items-center gap-3 py-2.5">
                <span className="min-w-0 flex-1 truncate font-medium">{item.name}</span>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={busyKey === item.key}
                  className="border-green-600/50 text-green-700 hover:bg-green-600/10 dark:text-green-400"
                  onClick={() => void handleAdopt(item)}
                >
                  {t('discovered.adopt')}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </DialogContent>
    </Dialog>
  );
}
