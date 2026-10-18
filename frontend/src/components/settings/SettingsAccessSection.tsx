import { useCapabilities } from '../../auth/SessionProvider';
import { AccessAuditPanel } from './AccessAuditPanel';
import { AccessIngressPanel } from './AccessIngressPanel';
import { AccessProfilePanel } from './AccessProfilePanel';
import { AccessSessionsPanel } from './AccessSessionsPanel';
import { AccessUsersPanel } from './AccessUsersPanel';

/**
 * Settings → Users & Access. Every account sees its own profile and sessions; the
 * user list and the Home Assistant default role are for an administrator (the server
 * refuses them to anyone else, `app/auth/permissions.py`).
 */
export function SettingsAccessSection({ className }: { className?: string }) {
  const { isAdmin, hasProfile } = useCapabilities();
  return (
    <div className={`space-y-4 ${className ?? ''}`}>
      {hasProfile && <AccessProfilePanel />}
      {hasProfile && <AccessSessionsPanel />}
      {isAdmin && <AccessUsersPanel />}
      {isAdmin && <AccessIngressPanel />}
      {isAdmin && <AccessAuditPanel />}
    </div>
  );
}
