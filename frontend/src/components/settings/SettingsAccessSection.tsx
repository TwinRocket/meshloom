import { useEffect, useState } from 'react';

import { api } from '../../api';
import { useCapabilities } from '../../auth/SessionProvider';
import type { AccessSecurityNotice } from '../../auth/types';
import { AccessAuditPanel } from './AccessAuditPanel';
import { AccessEmailFactorPanel } from './AccessEmailFactorPanel';
import { AccessIngressPanel } from './AccessIngressPanel';
import { AccessPairingPanel } from './AccessPairingPanel';
import { AccessPasskeysPanel } from './AccessPasskeysPanel';
import { AccessProfilePanel } from './AccessProfilePanel';
import { AccessPublicAddressesPanel } from './AccessPublicAddressesPanel';
import { AccessSecurityPanel } from './AccessSecurityPanel';
import { AccessSessionsPanel } from './AccessSessionsPanel';
import { AccessTokensPanel } from './AccessTokensPanel';
import { AccessSecurityNoticeBanner } from './AccessTrustedNetworksPanel';
import { AccessUsersPanel } from './AccessUsersPanel';

/**
 * Settings → Users & Access. Every account sees its own profile and sessions; the
 * user list, the public addresses and the Host guard, the Home Assistant default role,
 * the audit log and the Access security notice are for an administrator (the server
 * refuses them to anyone else, `app/auth/permissions.py`). How people sign in (session
 * lifetime, client addresses, login-free on the local network, IP ban, OpenID Connect,
 * the e-mail channel of the sign-in mails) is in Settings → Connection
 * (`SettingsConnectionSection`); each account's own e-mail sign-in opt-in is here
 * (`AccessEmailFactorPanel`).
 */
export function SettingsAccessSection({ className }: { className?: string }) {
  const { isAdmin, hasProfile } = useCapabilities();
  const [notices, setNotices] = useState<AccessSecurityNotice[]>([]);
  useEffect(() => {
    if (!isAdmin) return;
    let cancelled = false;
    api
      .getAuthSettings()
      .then((settings) => {
        if (!cancelled) setNotices(settings.security_notices ?? []);
      })
      .catch(() => {
        // Not readable (older server, refused): no banner.
      });
    return () => {
      cancelled = true;
    };
  }, [isAdmin]);
  return (
    <div className={`space-y-4 ${className ?? ''}`}>
      {isAdmin && <AccessSecurityNoticeBanner notices={notices} />}
      {hasProfile && <AccessProfilePanel />}
      {hasProfile && <AccessSecurityPanel />}
      {hasProfile && <AccessPasskeysPanel />}
      {hasProfile && <AccessEmailFactorPanel />}
      {hasProfile && <AccessSessionsPanel />}
      {hasProfile && <AccessTokensPanel />}
      {isAdmin && <AccessPairingPanel />}
      {isAdmin && <AccessUsersPanel />}
      {isAdmin && <AccessPublicAddressesPanel />}
      {isAdmin && <AccessIngressPanel />}
      {isAdmin && <AccessAuditPanel />}
    </div>
  );
}
