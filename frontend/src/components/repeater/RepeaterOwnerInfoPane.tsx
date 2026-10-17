import { useTranslation } from 'react-i18next';
import { RepeaterPane, NotFetched, KvRow } from './repeaterPaneShared';
import type { RepeaterOwnerInfoResponse, PaneState } from '../../types';

function LabeledBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="py-0.5">
      <span className="text-sm text-muted-foreground whitespace-nowrap">{label}</span>
      <p className="text-sm font-medium mt-0.5 wrap-break-word">{value}</p>
    </div>
  );
}

export function OwnerInfoPane({
  data,
  state,
  onRefresh,
  disabled,
}: {
  data: RepeaterOwnerInfoResponse | null;
  state: PaneState;
  onRefresh: () => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <RepeaterPane
      title={t('repeater.ownerInfo')}
      state={state}
      onRefresh={onRefresh}
      disabled={disabled}
    >
      {!data ? (
        <NotFetched />
      ) : (
        <div className="space-y-1">
          <LabeledBlock label={t('repeater.ownerInfo')} value={data.owner_info ?? '—'} />
          <KvRow label={t('repeater.firmware')} value={data.firmware_version ?? '—'} />
          {data.name && <KvRow label={t('repeater.name')} value={data.name} />}
          <KvRow label={t('repeater.guestPassword')} value={data.guest_password ?? '—'} />
        </div>
      )}
    </RepeaterPane>
  );
}
