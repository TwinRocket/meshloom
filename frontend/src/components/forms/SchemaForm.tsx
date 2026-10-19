import { useId, useMemo, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import {
  buildFormModel,
  collectValues,
  fieldLabel,
  initialDraft,
  pickText,
  type Draft,
  type FieldErrors,
  type FieldModel,
  type JsonObject,
  type LabelResolver,
  type SecretMode,
} from './schemaFormModel';

export interface SchemaFormProps {
  /** A JSON Schema of the subset documented in `frontend/AGENTS.md` (root `type: object`). */
  schema: unknown;
  /** The stored plain values. A `writeOnly` field never reads from here. */
  values?: JsonObject;
  /** Names of the `writeOnly` fields that already hold a value on the server. */
  secretsSet?: readonly string[];
  resolver: LabelResolver;
  /** Resolves with the saved state, rejects to keep the form as typed. */
  onSubmit: (values: JsonObject) => void | Promise<void>;
  submitLabel: string;
  /** Server validation errors, by field name. */
  errors?: FieldErrors;
  disabled?: boolean;
  /** Fields to highlight (for example the required ones still empty). */
  flagged?: readonly string[];
  /** Prefix of the field ids (several forms may share a page). */
  idPrefix?: string;
}

const FIELD_CLASS = 'space-y-1';

/**
 * A form rendered from a JSON Schema. Generic on purpose: nothing here knows about plugins.
 * Validation stays on the server (`errors` maps its answer to fields); the inputs only carry
 * the native `required`, `min`, `max`, `minlength`, `maxlength` and `pattern` hints.
 */
export function SchemaForm({
  schema,
  values,
  secretsSet = [],
  resolver,
  onSubmit,
  submitLabel,
  errors,
  disabled = false,
  flagged = [],
  idPrefix,
}: SchemaFormProps) {
  const { t, i18n } = useTranslation();
  const language = i18n.language;
  const autoId = useId();
  const prefix = idPrefix ?? `schema-form-${autoId}`;
  const model = useMemo(() => buildFormModel(schema), [schema]);
  const fields = useMemo(
    () => model.entries.flatMap((entry) => (entry.field ? [entry.field] : [])),
    [model]
  );
  const [drafts, setDrafts] = useState<Record<string, Draft>>(() =>
    Object.fromEntries(fields.map((f) => [f.name, initialDraft(f, values?.[f.name])]))
  );
  const [modes, setModes] = useState<Record<string, SecretMode>>({});
  const [busy, setBusy] = useState(false);

  if (model.rootProblem) {
    return (
      <p role="alert" data-testid="schema-form-unsupported" className="text-sm text-destructive">
        {t('schemaForm.rootUnsupported', { keyword: model.rootProblem })}
      </p>
    );
  }

  const setDraft = (name: string, value: Draft) =>
    setDrafts((prev) => ({ ...prev, [name]: value }));
  const setMode = (name: string, mode: SecretMode) => {
    setModes((prev) => ({ ...prev, [name]: mode }));
    // Leaving or entering replace mode always starts from an empty value.
    setDraft(name, '');
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      await onSubmit(collectValues(fields, drafts, modes));
    } catch {
      // The caller reports it (and maps field errors); plain fields keep what was typed.
    } finally {
      // Saved or refused, no secret stays in the page: the server may have stored it
      // even when a later step failed, so retyping is safer than keeping it.
      setModes({});
      setDrafts((prev) => {
        const next = { ...prev };
        for (const field of fields) if (field.secret) next[field.name] = '';
        return next;
      });
      setBusy(false);
    }
  };

  const off = disabled || busy;

  return (
    <form onSubmit={submit} className="space-y-4">
      {model.entries.map((entry) => {
        if (entry.unsupported) {
          return (
            <p
              key={entry.unsupported.name}
              role="alert"
              data-testid="schema-form-unsupported"
              className="rounded-md border border-destructive/50 bg-destructive/10 p-2 text-sm text-destructive"
            >
              {t('schemaForm.unsupported', {
                name: entry.unsupported.name,
                keyword: entry.unsupported.keyword,
              })}
            </p>
          );
        }
        const field = entry.field;
        const id = `${prefix}-${field.name}`;
        const label = fieldLabel(field, resolver, language);
        const description =
          resolver.description?.(field.name, field.schema) ?? pickText(field.description, language);
        const fieldErrors = errors?.[field.name] ?? [];
        const describedBy = [
          description ? `${id}-help` : null,
          fieldErrors.length > 0 ? `${id}-error` : null,
        ]
          .filter(Boolean)
          .join(' ');
        const common = {
          id,
          disabled: off,
          'aria-describedby': describedBy || undefined,
          'aria-invalid': fieldErrors.length > 0 || undefined,
          required: field.required && field.kind !== 'boolean',
        };
        return (
          <div
            key={field.name}
            className={FIELD_CLASS}
            data-field={field.name}
            data-flagged={flagged.includes(field.name) || undefined}
          >
            <Label
              htmlFor={field.kind === 'enumArray' ? undefined : id}
              className={flagged.includes(field.name) ? 'text-warning' : undefined}
            >
              {label}
              {field.required && (
                <span aria-hidden="true" className="ml-0.5 text-destructive">
                  *
                </span>
              )}
              {field.required && <span className="sr-only"> {t('schemaForm.required')}</span>}
            </Label>
            <FieldInput
              field={field}
              common={common}
              draft={drafts[field.name]}
              onDraft={(value) => setDraft(field.name, value)}
              secretSet={secretsSet.includes(field.name)}
              mode={modes[field.name] ?? 'keep'}
              onMode={(mode) => setMode(field.name, mode)}
              resolver={resolver}
              label={label}
            />
            {description && (
              <p id={`${id}-help`} className="text-xs text-muted-foreground">
                {description}
              </p>
            )}
            {fieldErrors.length > 0 && (
              <p id={`${id}-error`} role="alert" className="text-xs text-destructive">
                {fieldErrors.join(' ')}
              </p>
            )}
          </div>
        );
      })}
      {fields.length > 0 && (
        <Button type="submit" disabled={off}>
          {submitLabel}
        </Button>
      )}
    </form>
  );
}

interface CommonProps {
  id: string;
  disabled: boolean;
  'aria-describedby'?: string;
  'aria-invalid'?: true;
  required: boolean;
}

function FieldInput({
  field,
  common,
  draft,
  onDraft,
  secretSet,
  mode,
  onMode,
  resolver,
  label,
}: {
  field: FieldModel;
  common: CommonProps;
  draft: Draft;
  onDraft: (value: Draft) => void;
  secretSet: boolean;
  mode: SecretMode;
  onMode: (mode: SecretMode) => void;
  resolver: LabelResolver;
  label: string;
}) {
  const { t } = useTranslation();
  const optionText = (value: string | number) =>
    resolver.option?.(field.name, value) ?? String(value);

  if (field.secret) {
    // Write-only: the value is never displayed; "set" only says one exists.
    if (secretSet && mode === 'keep') {
      return (
        <div className="flex flex-wrap items-center gap-2" id={common.id} tabIndex={-1}>
          <span className="text-sm text-muted-foreground">{t('schemaForm.secretSet')}</span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={common.disabled}
            onClick={() => onMode('replace')}
            aria-label={t('schemaForm.replaceFor', { name: label })}
          >
            {t('schemaForm.replace')}
          </Button>
          {!field.required && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={common.disabled}
              onClick={() => onMode('clear')}
              aria-label={t('schemaForm.clearFor', { name: label })}
            >
              {t('schemaForm.clear')}
            </Button>
          )}
        </div>
      );
    }
    if (mode === 'clear') {
      return (
        <div className="flex flex-wrap items-center gap-2" id={common.id} tabIndex={-1}>
          <span className="text-sm text-muted-foreground">{t('schemaForm.willClear')}</span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={common.disabled}
            onClick={() => onMode('keep')}
          >
            {t('schemaForm.undo')}
          </Button>
        </div>
      );
    }
    return (
      <div className="space-y-1">
        <div className="flex gap-2">
          <Input
            {...common}
            // Replacing a stored value may be left empty: the old value is kept.
            required={common.required && !secretSet}
            type="password"
            autoComplete="new-password"
            value={String(draft)}
            maxLength={field.maxLength}
            minLength={field.minLength}
            onChange={(e) => onDraft(e.target.value)}
          />
          {secretSet && (
            <Button type="button" variant="outline" onClick={() => onMode('keep')}>
              {t('schemaForm.cancel')}
            </Button>
          )}
        </div>
        {secretSet && <p className="text-xs text-muted-foreground">{t('schemaForm.keepHint')}</p>}
      </div>
    );
  }

  switch (field.kind) {
    case 'boolean':
      return (
        <input
          {...common}
          type="checkbox"
          className="ml-2 h-4 w-4 align-middle"
          checked={draft === true}
          onChange={(e) => onDraft(e.target.checked)}
        />
      );
    case 'enumArray':
      return (
        <div
          role="group"
          aria-labelledby={undefined}
          aria-label={label}
          id={common.id}
          className="space-y-1"
        >
          {(field.enum ?? []).map((value) => {
            const checked = Array.isArray(draft) && draft.includes(String(value));
            return (
              <label key={String(value)} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  disabled={common.disabled}
                  checked={checked}
                  onChange={(e) => {
                    const current = Array.isArray(draft) ? draft : [];
                    onDraft(
                      e.target.checked
                        ? [...current, String(value)]
                        : current.filter((v) => v !== String(value))
                    );
                  }}
                />
                <span>{optionText(value)}</span>
              </label>
            );
          })}
        </div>
      );
    case 'stringArray':
      return (
        <textarea
          {...common}
          rows={3}
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-base focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring md:text-sm"
          value={String(draft)}
          onChange={(e) => onDraft(e.target.value)}
          placeholder={t('schemaForm.onePerLine')}
        />
      );
    default:
  }

  if (field.enum) {
    return (
      <select
        {...common}
        className="h-10 w-full rounded-md border border-input bg-background px-2 text-base md:text-sm"
        value={String(draft)}
        onChange={(e) => onDraft(e.target.value)}
      >
        {(!field.required || draft === '') && (
          <option value="">{field.required ? '' : t('schemaForm.none')}</option>
        )}
        {field.enum.map((value) => (
          <option key={String(value)} value={String(value)}>
            {optionText(value)}
          </option>
        ))}
      </select>
    );
  }

  if (field.kind === 'integer' || field.kind === 'number') {
    return (
      <Input
        {...common}
        type="number"
        inputMode={field.kind === 'integer' ? 'numeric' : 'decimal'}
        step={field.kind === 'integer' ? 1 : 'any'}
        min={field.minimum}
        max={field.maximum}
        value={String(draft)}
        onChange={(e) => onDraft(e.target.value)}
      />
    );
  }

  const htmlType =
    field.format === 'password'
      ? 'password'
      : field.format === 'uri'
        ? 'url'
        : field.format === 'email'
          ? 'email'
          : 'text';
  return (
    <Input
      {...common}
      type={htmlType}
      autoComplete={htmlType === 'password' ? 'new-password' : undefined}
      minLength={field.minLength}
      maxLength={field.maxLength}
      pattern={field.pattern}
      value={String(draft)}
      onChange={(e) => onDraft(e.target.value)}
    />
  );
}
