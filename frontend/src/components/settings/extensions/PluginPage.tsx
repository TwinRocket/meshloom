import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../../../api';
import { Button } from '../../ui/button';
import { DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '../../ui/dialog';
import { toast } from '../../ui/sonner';
import { ConsentWarnings, PermissionList } from './PermissionList';
import { httpHosts, httpLanHosts, localizedText } from './pluginsShared';
import type { StoreReadme, StoreRepository } from './pluginsTypes';

/** Where a problem with a plugin from the Store is reported when it has no repository. */
export const REPORT_ADDRESS = 'store@meshloom.app';

/**
 * Only a repository the server validated as `owner/name` makes a URL: the issue tracker
 * of that GitHub repository. Nothing the plugin wrote is ever turned into a link.
 */
export function issuesUrl(repository: string): string | null {
  return /^[A-Za-z0-9][A-Za-z0-9-]*\/[A-Za-z0-9._-]+$/.test(repository)
    ? `https://github.com/${repository}/issues`
    : null;
}

/** The block pasted in a report: what identifies the release, never a config value. */
export function formatPluginReport(repo: StoreRepository): string {
  const entry = repo.entry;
  return [
    `plugin: ${entry?.plugin_id ?? '-'} ${entry?.version ?? '-'} (custom)`,
    `repository: ${repo.display_name}`,
    `tag: ${entry?.tag ?? '-'}`,
    `sha256: ${entry?.archive_sha256 ?? '-'}`,
    `compat: ${JSON.stringify(entry?.compat ?? null)}`,
    `compatible_with_this_meshloom: ${entry ? String(entry.compatible) : '-'}`,
    `last_check: ${repo.checked_at ?? '-'}`,
    `last_error: ${repo.last_error ?? '-'}`,
    `installed: ${repo.instances.map((i) => `${i.instance_id} ${i.version}`).join(', ') || '-'}`,
  ].join('\n');
}

/**
 * The page of a plugin from a custom repository. Everything the author wrote (name,
 * summary, README, host names) is untrusted: it is rendered as text only. The README sits
 * in a `<pre>`: no Markdown, no HTML, no link, no image.
 */
export function PluginPage({
  repo,
  canManage,
  canInstall,
  onInstall,
  onClose,
}: {
  repo: StoreRepository;
  canManage: boolean;
  canInstall: boolean;
  onInstall: () => void;
  onClose: () => void;
}) {
  const { t, i18n } = useTranslation();
  const entry = repo.entry;
  const [readme, setReadme] = useState<StoreReadme | null>(null);
  const [readmeError, setReadmeError] = useState<string | null>(null);
  const [report, setReport] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .getStoreReadme(repo.repository, i18n.language)
      .then((data) => alive && setReadme(data))
      .catch((err) => alive && setReadmeError(formatApiError(err, t)));
    return () => {
      alive = false;
    };
  }, [repo.repository, i18n.language, t]);

  if (!entry) return null;
  const name = localizedText(entry.name, i18n.language) || entry.plugin_id;
  const hosts = httpHosts(entry.permissions);
  const lanHosts = httpLanHosts(entry.permissions);
  const url = issuesUrl(repo.display_name);

  const copyReport = async () => {
    const text = formatPluginReport(repo);
    try {
      await navigator.clipboard.writeText(text);
      toast.success(t('pluginStore.page.diagnosticCopied'));
    } catch {
      // No clipboard over plain HTTP: show the block so it can be selected.
      setReport(text);
    }
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle className="break-words">{name}</DialogTitle>
        <DialogDescription className="break-words">
          {entry.plugin_id} · v{entry.version} ({entry.tag}) · {repo.display_name}
        </DialogDescription>
      </DialogHeader>

      <div className="space-y-4 text-sm" data-testid="plugin-page">
        <div className="flex flex-wrap gap-1.5">
          <span className="rounded bg-muted px-1.5 py-0.5 text-xs font-medium">
            {t('pluginStore.card.custom')}
          </span>
          <span
            className="rounded bg-amber-500/15 px-1.5 py-0.5 text-xs font-medium"
            data-testid="plugin-page-unreviewed"
          >
            {t('pluginStore.card.unreviewed')}
          </span>
          {!entry.compatible && (
            <span className="rounded bg-destructive/15 px-1.5 py-0.5 text-xs font-medium text-destructive">
              {t('pluginStore.card.incompatibleBadge')}
            </span>
          )}
        </div>
        <p className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3">
          {t('settings.extensions.store.warning')}
        </p>
        {!entry.compatible && (
          <p role="alert" className="text-destructive">
            {t('pluginStore.card.incompatible')}
          </p>
        )}
        <p className="break-words text-muted-foreground">
          {localizedText(entry.summary, i18n.language)}
        </p>

        <section className="space-y-1">
          <h3 className="font-medium">{t('pluginStore.page.readme')}</h3>
          {readmeError && (
            <p role="alert" className="text-destructive">
              {readmeError}
            </p>
          )}
          {!readme && !readmeError && (
            <p className="text-muted-foreground">{t('settings.extensions.loading')}</p>
          )}
          {readme && readme.text === '' && (
            <p className="text-muted-foreground">{t('pluginStore.page.noReadme')}</p>
          )}
          {readme && readme.text !== '' && (
            <>
              <p className="text-xs text-muted-foreground">
                {t('pluginStore.page.readmeNote', { lang: readme.lang ?? '' })}
              </p>
              <pre
                data-testid="plugin-readme"
                className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-muted/40 p-3 font-mono text-xs"
              >
                {readme.text}
              </pre>
            </>
          )}
        </section>

        <section className="space-y-2">
          <h3 className="font-medium">{t('pluginStore.page.permissions')}</h3>
          <PermissionList declared={entry.permissions} />
          <ConsentWarnings permissions={entry.permissions} manifest={{ privacy: entry.privacy }} />
          <div data-testid="plugin-page-hosts">
            <div className="font-medium">{t('pluginStore.page.hosts')}</div>
            {hosts.length + lanHosts.length === 0 ? (
              <p className="text-muted-foreground">{t('pluginStore.page.hostsNone')}</p>
            ) : (
              <ul className="list-disc pl-5 font-mono text-xs">
                {[...hosts, ...lanHosts].map((host) => (
                  <li key={host} className="break-all">
                    {host}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>

        <section className="space-y-1">
          <h3 className="font-medium">{t('pluginStore.page.versions')}</h3>
          <p className="text-muted-foreground">
            {t('pluginStore.page.latest', { version: entry.version, tag: entry.tag })}
          </p>
          {repo.instances.length > 0 ? (
            <ul className="list-disc pl-5 text-muted-foreground">
              {repo.instances.map((i) => (
                <li key={i.instance_id} className="break-words">
                  {t('pluginStore.page.installedVersion', { label: i.label, version: i.version })}
                  {i.hold && (
                    <span className="block text-xs" data-testid="plugin-page-hold">
                      {t(`pluginStore.updates.hold.${i.hold}`, { label: i.label })}
                    </span>
                  )}
                  {(i.dropped_fields?.length ?? 0) > 0 && (
                    <span className="block text-xs" data-testid="plugin-page-dropped">
                      {t('pluginStore.updates.dropped', {
                        label: i.label,
                        fields: (i.dropped_fields ?? []).join(', '),
                      })}
                    </span>
                  )}
                  {i.auto_update && i.auto_hold && (
                    <span className="block text-xs" data-testid="plugin-page-auto-held">
                      {t(`pluginStore.updates.autoHeld.${i.auto_hold}`)}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-muted-foreground">{t('pluginStore.page.notInstalled')}</p>
          )}
          {entry.archive_sha256 && (
            <p className="break-all font-mono text-xs text-muted-foreground">
              sha256 {entry.archive_sha256}
            </p>
          )}
        </section>

        <section className="space-y-1">
          <h3 className="font-medium">{t('pluginStore.page.reportTitle')}</h3>
          <p className="text-muted-foreground">
            {url ? t('pluginStore.page.reportIssues') : t('pluginStore.page.reportMail')}{' '}
            {url && (
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="break-all underline"
                data-testid="plugin-report-link"
              >
                {url}
              </a>
            )}
            {t('pluginStore.page.reportAddress', { address: REPORT_ADDRESS })}
          </p>
          <Button size="sm" variant="outline" onClick={() => void copyReport()}>
            {t('pluginStore.page.copyDiagnostic')}
          </Button>
          {report && (
            <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-md border border-border p-2 font-mono text-xs">
              {report}
            </pre>
          )}
        </section>
      </div>

      <DialogFooter className="gap-2">
        <Button variant="outline" onClick={onClose}>
          {t('pluginStore.page.close')}
        </Button>
        {canManage && (
          <Button onClick={onInstall} disabled={!canInstall || !entry.compatible}>
            {t('settings.extensions.store.install')}
          </Button>
        )}
      </DialogFooter>
    </>
  );
}
