#!/usr/bin/env node
/**
 * Fails the build when a cold load needs too many files at once (MSHL-470).
 *
 * Through Meshloom Remote the relay refuses a request beyond 64 open at a time, on one instance
 * connection, with the API burst, the WebSocket and any other open tab counted in. Reads the
 * production build: the files the entry needs before any code runs, plus the one file of the
 * active language. The API burst is not counted here: about 20 requests when this was written.
 *
 * Usage: node scripts/check-cold-load.mjs [dist directory]
 */
import { readdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

// What the entry pulls in was 22 files when this was written: room to grow, far from 64.
const MAX_ENTRY_FILES = 32;
const MAX_FILES_PER_LANGUAGE = 1;
const LANGUAGES = ['en', 'fr'];

const dist = resolve(process.argv[2] ?? 'dist');
const html = readFileSync(join(dist, 'index.html'), 'utf8');
const assets = join(dist, 'assets');

const tags = html.match(/<(?:script|link)\b[^>]*>/g) ?? [];
const pending = tags.flatMap((tag) => {
  const loaded = /type="module"|rel="(?:modulepreload|stylesheet)"/.test(tag);
  const url = /(?:src|href)="\.\/assets\/([^"]+)"/.exec(tag)?.[1];
  return loaded && url ? [url] : [];
});

const entryFiles = new Set();
while (pending.length > 0) {
  const file = pending.pop();
  if (entryFiles.has(file)) continue;
  entryFiles.add(file);
  if (!file.endsWith('.js')) continue;
  const code = readFileSync(join(assets, file), 'utf8');
  for (const match of code.matchAll(/\bimport\s*(?:[\w$*{},\s]+?\s*from\s*)?["']\.\/([^"']+\.js)["']/g)) {
    pending.push(match[1]);
  }
}

const problems = [];
if (entryFiles.size === 0) problems.push('no entry file found in index.html');
if (entryFiles.size > MAX_ENTRY_FILES) {
  problems.push(`the entry needs ${entryFiles.size} files, the budget is ${MAX_ENTRY_FILES}`);
}
const names = readdirSync(assets);
for (const language of LANGUAGES) {
  const files = names.filter((name) =>
    new RegExp(`(?:^|\\.)${language}-[\\w-]{8}\\.js$`).test(name)
  );
  // Zero means the files were renamed and this check went blind.
  if (files.length === 0) problems.push(`no language file found for ${language}`);
  if (files.length > MAX_FILES_PER_LANGUAGE) {
    problems.push(
      `${language} loads as ${files.length} files, the budget is ${MAX_FILES_PER_LANGUAGE}: ` +
        'language files must stay imported together (src/i18n/bundle.*.ts)'
    );
  }
}

if (problems.length > 0) {
  console.error(`Cold load check failed:\n- ${problems.join('\n- ')}`);
  process.exit(1);
}
console.log(`Cold load check passed: ${entryFiles.size} entry files, one file per language.`);
