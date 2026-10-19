/**
 * The pure half of `SchemaForm`: reads the supported subset of JSON Schema 2020-12 into
 * field descriptors, resolves labels and turns the typed drafts back into values. It
 * imports nothing from the plugin code (Notifications renders its channel types with it).
 */

export type JsonObject = Record<string, unknown>;

/** A field's display text: a plain string, or a language map (`{ en, fr }`). */
export type LocalizedText = string | Record<string, string>;

export type FieldKind = 'string' | 'integer' | 'number' | 'boolean' | 'stringArray' | 'enumArray';

export interface FieldModel {
  name: string;
  kind: FieldKind;
  required: boolean;
  /** `anyOf: [<type>, {type: null}]`: the field may be left empty. */
  nullable: boolean;
  /** `writeOnly: true`: never displayed, sent only when set or replaced. */
  secret: boolean;
  title?: LocalizedText;
  description?: LocalizedText;
  default?: unknown;
  enum?: Array<string | number>;
  format?: 'uri' | 'email' | 'password';
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  minimum?: number;
  maximum?: number;
  minItems?: number;
  maxItems?: number;
  /** The raw property, handed to the label resolver. */
  schema: JsonObject;
}

export interface UnsupportedField {
  name: string;
  /** The keyword (or `type`) the renderer does not handle. */
  keyword: string;
}

export type FormEntry =
  | { field: FieldModel; unsupported?: undefined }
  | { field?: undefined; unsupported: UnsupportedField };

export interface FormModel {
  entries: FormEntry[];
  /** Set when the root itself is outside the subset (not an object schema, unknown keyword). */
  rootProblem: string | null;
}

/** What a caller supplies to name fields: i18next keys, language maps, or both. */
export interface LabelResolver {
  label: (name: string, schema: JsonObject) => string | undefined;
  description?: (name: string, schema: JsonObject) => string | undefined;
  /** The text of one enum value; defaults to the value itself. */
  option?: (name: string, value: string | number) => string | undefined;
}

const ROOT_KEYS = new Set([
  '$schema',
  '$id',
  '$defs',
  'type',
  'title',
  'description',
  'properties',
  'required',
  'additionalProperties',
  '$comment',
]);
const COMMON_KEYS = [
  'type',
  'title',
  'description',
  'default',
  'writeOnly',
  'examples',
  '$comment',
];
const KEYS_BY_TYPE: Record<string, string[]> = {
  string: ['enum', 'minLength', 'maxLength', 'pattern', 'format'],
  integer: ['enum', 'minimum', 'maximum'],
  number: ['enum', 'minimum', 'maximum'],
  boolean: [],
  array: ['items', 'minItems', 'maxItems', 'uniqueItems'],
};
const ITEM_KEYS = ['type', 'enum', 'title', 'minLength', 'maxLength', 'description'];
const FORMATS = new Set(['uri', 'email', 'password']);
const MAX_REF_DEPTH = 8;

const isObject = (value: unknown): value is JsonObject =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

function localRef(root: JsonObject, ref: string): JsonObject | null {
  if (!ref.startsWith('#/')) return null;
  let node: unknown = root;
  for (const part of ref.slice(2).split('/')) {
    node = isObject(node) ? node[part.replace(/~1/g, '/').replace(/~0/g, '~')] : undefined;
  }
  return isObject(node) ? node : null;
}

/** Inlines local `$ref`s (siblings such as `default` or `title` win over the target). */
function deref(root: JsonObject, schema: JsonObject): JsonObject | string {
  let current = schema;
  for (let depth = 0; depth < MAX_REF_DEPTH; depth += 1) {
    const ref = current.$ref;
    if (typeof ref !== 'string') return current;
    const target = localRef(root, ref);
    if (!target) return '$ref';
    const { $ref: _ref, ...siblings } = current;
    current = { ...target, ...siblings };
  }
  return '$ref';
}

/** `anyOf: [<type>, {type: null}]` -> the type, nullable. Anything else under `anyOf` is out. */
function unwrapNullable(
  root: JsonObject,
  schema: JsonObject
): { schema: JsonObject; nullable: boolean } | string {
  const { anyOf, ...rest } = schema;
  if (anyOf === undefined) return { schema, nullable: false };
  if (!Array.isArray(anyOf) || anyOf.length !== 2) return 'anyOf';
  const resolved = anyOf.map((branch) => (isObject(branch) ? deref(root, branch) : 'anyOf'));
  const real = resolved.filter((b) => isObject(b) && b.type !== 'null');
  const nulls = resolved.filter((b) => isObject(b) && b.type === 'null');
  if (real.length !== 1 || nulls.length !== 1) return 'anyOf';
  return { schema: { ...(real[0] as JsonObject), ...rest }, nullable: true };
}

function firstUnknownKey(schema: JsonObject, allowed: string[]): string | null {
  const set = new Set(allowed);
  for (const key of Object.keys(schema)) {
    if (!set.has(key)) return key;
  }
  return null;
}

function stringList(value: unknown): Array<string | number> | null {
  return Array.isArray(value) && value.every((v) => typeof v === 'string' || typeof v === 'number')
    ? (value as Array<string | number>)
    : null;
}

const num = (value: unknown): number | undefined => (typeof value === 'number' ? value : undefined);

function readLocalized(value: unknown): LocalizedText | undefined {
  if (typeof value === 'string') return value;
  if (isObject(value) && Object.values(value).every((v) => typeof v === 'string')) {
    return value as Record<string, string>;
  }
  return undefined;
}

function readField(root: JsonObject, name: string, raw: unknown, required: boolean): FormEntry {
  const unsupported = (keyword: string): FormEntry => ({ unsupported: { name, keyword } });
  if (!isObject(raw)) return unsupported('schema');
  const dereffed = deref(root, raw);
  if (typeof dereffed === 'string') return unsupported(dereffed);
  const unwrapped = unwrapNullable(root, dereffed);
  if (typeof unwrapped === 'string') return unsupported(unwrapped);
  const { schema, nullable } = unwrapped;
  const type = schema.type;
  if (typeof type !== 'string' || !(type in KEYS_BY_TYPE)) {
    if (typeof type === 'string') return unsupported(`type: ${type}`);
    return unsupported(firstUnknownKey(schema, COMMON_KEYS) ?? 'type');
  }
  const bad = firstUnknownKey(schema, [...COMMON_KEYS, ...KEYS_BY_TYPE[type]]);
  if (bad) return unsupported(bad);

  const base = {
    name,
    required,
    nullable,
    secret: schema.writeOnly === true,
    title: readLocalized(schema.title),
    description: readLocalized(schema.description),
    default: schema.default,
    schema,
  };
  if (type === 'array') {
    const items = isObject(schema.items) ? deref(root, schema.items) : 'items';
    if (typeof items === 'string') return unsupported('items');
    const badItem = firstUnknownKey(items, ITEM_KEYS);
    if (badItem) return unsupported(`items.${badItem}`);
    if (items.type !== 'string') return unsupported('items.type');
    const values = items.enum === undefined ? undefined : stringList(items.enum);
    if (values === null) return unsupported('items.enum');
    return {
      field: {
        ...base,
        kind: values ? 'enumArray' : 'stringArray',
        enum: values,
        minItems: num(schema.minItems),
        maxItems: num(schema.maxItems),
      },
    };
  }
  const values = schema.enum === undefined ? undefined : stringList(schema.enum);
  if (values === null) return unsupported('enum');
  const format = schema.format;
  if (format !== undefined && (typeof format !== 'string' || !FORMATS.has(format))) {
    return unsupported(`format: ${String(format)}`);
  }
  return {
    field: {
      ...base,
      kind: type as FieldKind,
      enum: values,
      format: format as FieldModel['format'],
      minLength: num(schema.minLength),
      maxLength: num(schema.maxLength),
      pattern: typeof schema.pattern === 'string' ? schema.pattern : undefined,
      minimum: num(schema.minimum),
      maximum: num(schema.maximum),
    },
  };
}

/** Reads a schema into field descriptors; anything outside the subset is an explicit entry. */
export function buildFormModel(schema: unknown): FormModel {
  if (!isObject(schema)) return { entries: [], rootProblem: 'schema' };
  // A root without `type` is read as an object (only `properties` matters).
  if (schema.type !== undefined && schema.type !== 'object') {
    return { entries: [], rootProblem: 'type' };
  }
  const bad = firstUnknownKey(schema, [...ROOT_KEYS]);
  if (bad) return { entries: [], rootProblem: bad };
  const properties = isObject(schema.properties) ? schema.properties : {};
  const required = new Set(Array.isArray(schema.required) ? schema.required.map(String) : []);
  return {
    entries: Object.entries(properties).map(([name, raw]) =>
      readField(schema, name, raw, required.has(name))
    ),
    rootProblem: null,
  };
}

// --- labels ---------------------------------------------------------------------

/** Picks a language from a text: exact tag, its base language, English, then any. */
export function pickText(text: LocalizedText | undefined, language: string): string | undefined {
  if (text === undefined) return undefined;
  if (typeof text === 'string') return text;
  const short = language.toLowerCase().split('-')[0];
  return text[language] ?? text[short] ?? text.en ?? Object.values(text)[0];
}

/** Titles and descriptions carried by the schema, in the reader's language (plugins). */
export function languageMapResolver(language: string): LabelResolver {
  return {
    label: (_name, schema) => pickText(readLocalized(schema.title), language),
    description: (_name, schema) => pickText(readLocalized(schema.description), language),
  };
}

/**
 * Titles and descriptions looked up as i18next keys, `<prefix>.<name>.label` and
 * `.description` (Notifications); a missing key falls back to the schema's own text.
 */
export function i18nKeyResolver(
  t: (key: string, options?: Record<string, unknown>) => string,
  prefix: string,
  language: string
): LabelResolver {
  const lookup = (key: string) => {
    const text = t(key, { defaultValue: '' });
    return text && text !== key ? text : undefined;
  };
  const fallback = languageMapResolver(language);
  return {
    label: (name, schema) => lookup(`${prefix}.${name}.label`) ?? fallback.label(name, schema),
    description: (name, schema) =>
      lookup(`${prefix}.${name}.description`) ?? fallback.description?.(name, schema),
    option: (name, value) => lookup(`${prefix}.${name}.options.${String(value)}`),
  };
}

/** Resolver text, then the schema `title`, then the property name. */
export function fieldLabel(field: FieldModel, resolver: LabelResolver, language: string): string {
  return resolver.label(field.name, field.schema) ?? pickText(field.title, language) ?? field.name;
}

// --- drafts ---------------------------------------------------------------------

/** What the user typed for one field, before it becomes a value. */
export type Draft = string | boolean | string[];

export function initialDraft(field: FieldModel, stored: unknown): Draft {
  if (field.secret) return '';
  const value = stored === undefined ? field.default : stored;
  switch (field.kind) {
    case 'boolean':
      return value === true;
    case 'stringArray':
      return Array.isArray(value) ? value.map(String).join('\n') : '';
    case 'enumArray':
      return Array.isArray(value) ? value.map(String) : [];
    default:
      return value === undefined || value === null ? '' : String(value);
  }
}

export type SecretMode = 'keep' | 'replace' | 'clear';

/**
 * The values to send. Empty optional fields are left out; a secret appears only when it is
 * replaced (its new value) or cleared (`""`): a kept or untouched secret is not part of
 * the result, which is what lets the server keep it.
 */
export function collectValues(
  fields: FieldModel[],
  drafts: Record<string, Draft>,
  secretModes: Record<string, SecretMode>
): JsonObject {
  const out: JsonObject = {};
  for (const field of fields) {
    const draft = drafts[field.name];
    if (field.secret) {
      const mode = secretModes[field.name] ?? 'keep';
      if (mode === 'clear') out[field.name] = '';
      else if (typeof draft === 'string' && draft !== '') out[field.name] = draft;
      continue;
    }
    switch (field.kind) {
      case 'boolean':
        out[field.name] = draft === true;
        break;
      case 'stringArray': {
        const lines = String(draft)
          .split('\n')
          .map((line) => line.trim())
          .filter(Boolean);
        if (lines.length > 0) out[field.name] = lines;
        break;
      }
      case 'enumArray':
        if (Array.isArray(draft) && draft.length > 0) out[field.name] = draft;
        break;
      case 'integer':
      case 'number': {
        const text = String(draft).trim();
        if (text === '') break;
        const value = Number(text);
        if (Number.isFinite(value)) out[field.name] = value;
        break;
      }
      default: {
        const text = String(draft);
        if (text !== '') out[field.name] = text;
      }
    }
  }
  return out;
}

// --- server errors --------------------------------------------------------------

export type FieldErrors = Record<string, string[]>;

/** Groups `{path, message}` pairs by field (a nested path keeps its first segment). */
export function groupFieldErrors(issues: Array<{ path: string; message: string }>): FieldErrors {
  const out: FieldErrors = {};
  for (const issue of issues) {
    const name = issue.path.replace(/^\/+/, '').split(/[./]/)[0] || '';
    (out[name] ??= []).push(issue.message);
  }
  return out;
}
