import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../../api';
import type { AccessSecurityNotice } from '../../auth/types';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../ui/tabs';
import { AccessClientAddressesPanel } from './AccessClientAddressesPanel';
import { AccessEmailSignInPanel } from './AccessEmailSignInPanel';
import { AccessIpBanPanel } from './AccessIpBanPanel';
import { AccessOidcPanel } from './AccessOidcPanel';
import { AccessSessionLifetimePanel } from './AccessSessionLifetimePanel';
import {
  AccessSecurityNoticeBanner,
  AccessTrustedNetworksPanel,
} from './AccessTrustedNetworksPanel';

/**
 * The tabs of Settings → Connection, in order. A new way to sign in or a new sign-in
 * policy adds its tab here; `email` (MSHL-426) is the SMTP profile of the e-mail code
 * and the sign-in links.
 */
export const CONNECTION_TABS = ['local', 'lan', 'bans', 'oidc', 'email'] as const;
export type ConnectionTab = (typeof CONNECTION_TABS)[number];

/**
 * Settings → Connection (administrator only): how people sign in. It groups the panels
 * that were in Users & Access, unchanged (the session lifetime and the client addresses
 * of the local sign-in, login-free on the local network, the IP ban), the OpenID
 * Connect provider (MSHL-427) and the e-mail channel of the e-mail code and sign-in
 * links (MSHL-426). The Access security notice is shown above the tabs.
 */
export function SettingsConnectionSection({ className }: { className?: string }) {
  const { t } = useTranslation();
  const [notices, setNotices] = useState<AccessSecurityNotice[]>([]);
  const [tab, setTab] = useState<ConnectionTab>('local');

  useEffect(() => {
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
  }, []);

  return (
    <div className={`space-y-4 ${className ?? ''}`}>
      <AccessSecurityNoticeBanner notices={notices} />
      <Tabs value={tab} onValueChange={(value) => setTab(value as ConnectionTab)}>
        <TabsList className="h-auto flex-wrap justify-start">
          {CONNECTION_TABS.map((id) => (
            <TabsTrigger key={id} value={id} data-testid={`connection-tab-${id}`}>
              {t(`settings.connection.tabs.${id}`)}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="local" className="space-y-4">
          <AccessSessionLifetimePanel />
          <AccessClientAddressesPanel />
        </TabsContent>
        <TabsContent value="lan" className="space-y-4">
          <AccessTrustedNetworksPanel onNotices={setNotices} />
        </TabsContent>
        <TabsContent value="bans" className="space-y-4">
          <AccessIpBanPanel />
        </TabsContent>
        <TabsContent value="oidc" className="space-y-4">
          <AccessOidcPanel />
        </TabsContent>
        <TabsContent value="email" className="space-y-4">
          <AccessEmailSignInPanel />
        </TabsContent>
      </Tabs>
    </div>
  );
}
