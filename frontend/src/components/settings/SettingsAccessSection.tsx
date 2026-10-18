import { useState } from 'react';

import { useCapabilities } from '../../auth/SessionProvider';
import type { AccessSecurityNotice } from '../../auth/types';
import { AccessAuditPanel } from './AccessAuditPanel';
import { AccessIngressPanel } from './AccessIngressPanel';
import { AccessProfilePanel } from './AccessProfilePanel';
import { AccessPublicAddressesPanel } from './AccessPublicAddressesPanel';
import { AccessSecurityPanel } from './AccessSecurityPanel';
import { AccessSessionsPanel } from './AccessSessionsPanel';
import { AccessTokensPanel } from './AccessTokensPanel';
import {
  AccessSecurityNoticeBanner,
  AccessTrustedNetworksPanel,
} from './AccessTrustedNetworksPanel';
import { AccessUsersPanel } from './AccessUsersPanel';

/**
 * Settings → Users & Access. Every account sees its own profile and sessions; the
 * user list, the trusted networks, the public addresses and the Host guard, the Home Assistant default role and the Access
 * security notice are for an administrator (the server refuses them to anyone else,
 * `app/auth/permissions.py`).
 */
export function SettingsAccessSection({ className }: { className?: string }) {
  const { isAdmin, hasProfile } = useCapabilities();
  const [notices, setNotices] = useState<AccessSecurityNotice[]>([]);
  return (
    <div className={`space-y-4 ${className ?? ''}`}>
      {isAdmin && <AccessSecurityNoticeBanner notices={notices} />}
      {hasProfile && <AccessProfilePanel />}
      {hasProfile && <AccessSecurityPanel />}
      {hasProfile && <AccessSessionsPanel />}
      {hasProfile && <AccessTokensPanel />}
      {isAdmin && <AccessUsersPanel />}
      {isAdmin && <AccessTrustedNetworksPanel onNotices={setNotices} />}
      {isAdmin && <AccessPublicAddressesPanel />}
      {isAdmin && <AccessIngressPanel />}
      {isAdmin && <AccessAuditPanel />}
    </div>
  );
}
