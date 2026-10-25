import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../../../api';
import type { NotificationChannel, NotificationTypeInfo } from '../../../notifications/types';
import type { FieldErrors } from '../../forms/schemaFormModel';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '../../ui/dialog';
import { toast } from '../../ui/sonner';
import { ChannelForm } from './ChannelForm';
import { fieldErrorsOf } from './notificationsShared';

/** Edits a saved channel: secrets stay "Unchanged" unless replaced or cleared. */
export function ChannelEditDialog({
  channel,
  entry,
  onClose,
  onSaved,
}: {
  channel: NotificationChannel;
  entry: NotificationTypeInfo;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [general, setGeneral] = useState<string | null>(null);
  const [errors, setErrors] = useState<FieldErrors>({});

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90dvh] w-[calc(100vw-1.5rem)] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {t('settings.notifications.channels.editTitle', { name: channel.name })}
          </DialogTitle>
          <DialogDescription>{t('settings.notifications.channels.editHelp')}</DialogDescription>
        </DialogHeader>
        <ChannelForm
          entry={entry}
          channel={channel}
          idPrefix={`edit-${channel.id.replace(/[^a-z0-9]/gi, '-')}`}
          submitLabel={t('settings.notifications.channels.save')}
          errors={errors}
          general={general}
          onSubmit={async (draft, config) => {
            setGeneral(null);
            setErrors({});
            try {
              await api.updateNotificationChannel(channel.id, {
                name: draft.name,
                language: draft.language,
                config,
                allow_private_network: draft.allow_private_network,
                include_message_text: entry.cloud ? draft.include_message_text : undefined,
                verify_tls: draft.verify_tls,
              });
            } catch (err) {
              // A legacy channel answers 409 `legacy_managed`: the 4.18.x setting still decides.
              setGeneral(formatApiError(err, t));
              setErrors(fieldErrorsOf(err, t('settings.notifications.channels.fieldInvalid')));
              throw err;
            }
            toast.success(t('settings.notifications.channels.saved'));
            onSaved();
            onClose();
          }}
        />
      </DialogContent>
    </Dialog>
  );
}
