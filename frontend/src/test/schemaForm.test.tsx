import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { SchemaForm } from '../components/forms/SchemaForm';
import {
  buildFormModel,
  collectValues,
  groupFieldErrors,
  i18nKeyResolver,
  languageMapResolver,
  type LabelResolver,
} from '../components/forms/schemaFormModel';
import i18n from '../i18n';
import pydanticSchema from './fixtures/schemaForm.pydantic.json';

const t = (key: string, options?: Record<string, unknown>) => i18n.t(key, options);

/** The subset a plugin's `config.schema.json` uses, with per-language titles. */
const pluginSchema = {
  type: 'object',
  required: ['city'],
  properties: {
    city: {
      type: 'string',
      title: { en: 'City', fr: 'Ville' },
      description: { en: 'Where', fr: 'Où' },
      minLength: 2,
      maxLength: 30,
    },
    api_key: { type: 'string', title: { en: 'API key', fr: 'Clé API' }, writeOnly: true },
    units: { type: 'string', enum: ['metric', 'imperial'], default: 'metric', title: 'Units' },
    days: { type: 'integer', minimum: 1, maximum: 7, default: 3, title: 'Days' },
    ratio: { type: 'number', minimum: 0, maximum: 1, title: 'Ratio' },
    verbose: { type: 'boolean', title: 'Verbose' },
    hosts: { type: 'array', items: { type: 'string' }, title: 'Hosts' },
    kinds: { type: 'array', items: { type: 'string', enum: ['a', 'b'] }, title: 'Kinds' },
    site: { type: 'string', format: 'uri', title: 'Site' },
    mail: { type: 'string', format: 'email', title: 'Mail' },
  },
};

const resolver = languageMapResolver('en');

function renderForm(
  props: Partial<React.ComponentProps<typeof SchemaForm>> & { schema: unknown },
  onSubmit = vi.fn()
) {
  render(<SchemaForm resolver={resolver} onSubmit={onSubmit} submitLabel="Save" {...props} />);
  return onSubmit;
}

describe('SchemaForm: the plugin subset', () => {
  it('renders every documented type with native hints', () => {
    renderForm({ schema: pluginSchema });
    const city = screen.getByLabelText(/City/) as HTMLInputElement;
    expect(city.required).toBe(true);
    expect(city.minLength).toBe(2);
    expect(city.maxLength).toBe(30);
    expect(screen.getByText('Where')).toBeInTheDocument();
    expect((screen.getByLabelText('Units') as HTMLSelectElement).value).toBe('metric');
    const days = screen.getByLabelText('Days') as HTMLInputElement;
    expect(days.type).toBe('number');
    expect(days.min).toBe('1');
    expect(days.max).toBe('7');
    expect(days.value).toBe('3');
    expect((screen.getByLabelText('Ratio') as HTMLInputElement).step).toBe('any');
    expect((screen.getByLabelText('Verbose') as HTMLInputElement).type).toBe('checkbox');
    expect(screen.getByLabelText('Hosts').tagName).toBe('TEXTAREA');
    expect(screen.getByLabelText('a')).toBeInTheDocument();
    expect((screen.getByLabelText('Site') as HTMLInputElement).type).toBe('url');
    expect((screen.getByLabelText('Mail') as HTMLInputElement).type).toBe('email');
  });

  it('sends typed values and leaves empty optional fields out', async () => {
    const onSubmit = renderForm({ schema: pluginSchema, values: { city: 'Paris' } });
    fireEvent.change(screen.getByLabelText('Days'), { target: { value: '5' } });
    fireEvent.click(screen.getByLabelText('Verbose'));
    fireEvent.change(screen.getByLabelText('Hosts'), {
      target: { value: 'a.example\n\n b.example ' },
    });
    fireEvent.click(screen.getByLabelText('b'));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(onSubmit.mock.calls[0][0]).toEqual({
      city: 'Paris',
      units: 'metric',
      days: 5,
      verbose: true,
      hosts: ['a.example', 'b.example'],
      kinds: ['b'],
    });
  });

  it('maps server errors to their field', () => {
    renderForm({ schema: pluginSchema, errors: { city: ['Too short.'] } });
    expect(screen.getByLabelText(/City/)).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByRole('alert')).toHaveTextContent('Too short.');
  });

  it('highlights flagged fields', () => {
    renderForm({ schema: pluginSchema, flagged: ['city'] });
    expect(document.querySelector('[data-field="city"]')).toHaveAttribute('data-flagged');
  });
});

describe('SchemaForm: write-only secrets', () => {
  it('never shows a stored value, even one passed in values', () => {
    renderForm({ schema: pluginSchema, values: { api_key: 'hunter2' } });
    expect((screen.getByLabelText(/API key/) as HTMLInputElement).value).toBe('');
    expect(document.body.innerHTML).not.toContain('hunter2');
  });

  it('sends nothing for a secret that is set and left alone', async () => {
    const onSubmit = renderForm({
      schema: pluginSchema,
      values: { city: 'Paris' },
      secretsSet: ['api_key'],
    });
    expect(screen.getByText(i18n.t('schemaForm.secretSet'))).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('api_key');
  });

  it('keeps the old value when Replace is opened and left empty', async () => {
    const onSubmit = renderForm({
      schema: pluginSchema,
      values: { city: 'Paris' },
      secretsSet: ['api_key'],
    });
    fireEvent.click(screen.getByRole('button', { name: new RegExp(i18n.t('schemaForm.replace')) }));
    expect(screen.getByText(i18n.t('schemaForm.keepHint'))).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('api_key');
  });

  it('sends a replacement, then holds none in the DOM after the save', async () => {
    const onSubmit = renderForm({
      schema: pluginSchema,
      values: { city: 'Paris' },
      secretsSet: ['api_key'],
    });
    fireEvent.click(screen.getByRole('button', { name: new RegExp(i18n.t('schemaForm.replace')) }));
    const input = screen.getAllByLabelText(/API key|Clé API/)[0] as HTMLInputElement;
    expect(input.type).toBe('password');
    fireEvent.change(input, { target: { value: 'new-secret' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(onSubmit.mock.calls[0][0]).toMatchObject({ api_key: 'new-secret' });
    await waitFor(() =>
      expect(screen.getByText(i18n.t('schemaForm.secretSet'))).toBeInTheDocument()
    );
    expect(document.body.innerHTML).not.toContain('new-secret');
  });

  it('keeps what was typed when the save fails', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error('no'));
    renderForm({ schema: pluginSchema, values: { city: 'Paris' } }, onSubmit);
    fireEvent.change(screen.getByLabelText(/API key/), { target: { value: 'typed' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled());
    expect((screen.getByLabelText(/API key/) as HTMLInputElement).value).toBe('typed');
  });

  it('clears a secret with "" and can undo', async () => {
    const onSubmit = renderForm({
      schema: pluginSchema,
      values: { city: 'Paris' },
      secretsSet: ['api_key'],
    });
    fireEvent.click(screen.getByRole('button', { name: new RegExp(i18n.t('schemaForm.clear')) }));
    fireEvent.click(screen.getByRole('button', { name: i18n.t('schemaForm.undo') }));
    fireEvent.click(screen.getByRole('button', { name: new RegExp(i18n.t('schemaForm.clear')) }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(onSubmit.mock.calls[0][0]).toMatchObject({ api_key: '' });
  });

  it('does not offer to clear a required secret', () => {
    renderForm({
      schema: {
        type: 'object',
        required: ['k'],
        properties: { k: { type: 'string', writeOnly: true, title: 'K' } },
      },
      secretsSet: ['k'],
    });
    expect(
      screen.queryByRole('button', { name: new RegExp(i18n.t('schemaForm.clear')) })
    ).toBeNull();
  });
});

describe('SchemaForm: shapes pydantic emits', () => {
  it('builds the model: SecretStr, Optional, $ref enum', () => {
    const { entries, rootProblem } = buildFormModel(pydanticSchema);
    expect(rootProblem).toBeNull();
    const byName = Object.fromEntries(entries.map((e) => [e.field?.name, e.field]));
    expect(byName.token).toMatchObject({ secret: true, required: true });
    expect(byName.note).toMatchObject({
      nullable: true,
      kind: 'string',
      maxLength: 200,
      required: false,
    });
    expect(byName.link).toMatchObject({ nullable: true, format: 'uri' });
    expect(byName.level).toMatchObject({ enum: ['info', 'warning'], default: 'info' });
    expect(byName.retries).toMatchObject({ kind: 'integer', minimum: 0, maximum: 10, default: 3 });
    expect(entries.every((e) => e.field)).toBe(true);
  });

  it('renders them with the i18n-key resolver', async () => {
    const keyed: LabelResolver = i18nKeyResolver(
      (key, options) =>
        key === 'ch.name.label'
          ? 'Nom du canal'
          : key === 'ch.level.options.info'
            ? 'Info !'
            : ((options?.defaultValue as string) ?? key),
      'ch',
      'fr'
    );
    const onSubmit = renderForm({ schema: pydanticSchema, resolver: keyed, secretsSet: [] });
    expect(screen.getByLabelText(/Nom du canal/)).toBeInTheDocument();
    // No key: falls back to the schema title, then to the name.
    expect(screen.getByLabelText(/Retries/)).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Info !' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Nom du canal/), { target: { value: 'main' } });
    fireEvent.change(screen.getByLabelText(/Token/), { target: { value: 's3cret' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(onSubmit.mock.calls[0][0]).toEqual({
      name: 'main',
      token: 's3cret',
      level: 'info',
      retries: 3,
      ratio: 0.5,
      enabled: true,
    });
  });

  it('leaves a nullable field empty as omitted', () => {
    const { entries } = buildFormModel(pydanticSchema);
    const fields = entries.flatMap((e) => (e.field ? [e.field] : []));
    const note = fields.find((f) => f.name === 'note')!;
    expect(collectValues([note], { note: '' }, {})).toEqual({});
  });
});

describe('SchemaForm: labels', () => {
  it('picks the language from a map, then English, and falls back to the name', () => {
    renderForm({ schema: pluginSchema, resolver: languageMapResolver('fr-CA') });
    expect(screen.getByLabelText(/Ville/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Clé API/)).toBeInTheDocument();
    expect(screen.getByLabelText('Units')).toBeInTheDocument();
  });

  it('falls back to the property name without title', () => {
    renderForm({ schema: { type: 'object', properties: { bare: { type: 'string' } } } });
    expect(screen.getByLabelText('bare')).toBeInTheDocument();
  });
});

describe('SchemaForm: unsupported keywords', () => {
  it('shows a typed error and still renders the other fields', () => {
    renderForm({
      schema: {
        type: 'object',
        properties: {
          ok: { type: 'string', title: 'Fine' },
          odd: { type: 'string', title: 'Odd', const: 'x' },
          deep: { type: 'object', properties: {} },
          pick: { oneOf: [{ type: 'string' }, { type: 'integer' }] },
          badref: { $ref: '#/$defs/missing' },
        },
      },
    });
    expect(screen.getByLabelText('Fine')).toBeInTheDocument();
    const alerts = screen.getAllByTestId('schema-form-unsupported');
    expect(alerts).toHaveLength(4);
    expect(alerts[0]).toHaveTextContent('odd');
    expect(alerts[0]).toHaveTextContent('const');
    expect(alerts[1]).toHaveTextContent('type: object');
    expect(alerts[2]).toHaveTextContent('oneOf');
    expect(alerts[3]).toHaveTextContent('$ref');
    expect(screen.queryByLabelText('Odd')).toBeNull();
  });

  it('refuses a root that is not an object schema', () => {
    renderForm({ schema: { type: 'string' } });
    expect(screen.getByTestId('schema-form-unsupported')).toHaveTextContent('type');
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
  });

  it('refuses an unknown root keyword', () => {
    renderForm({ schema: { type: 'object', patternProperties: {}, properties: {} } });
    expect(screen.getByTestId('schema-form-unsupported')).toHaveTextContent('patternProperties');
  });
});

describe('groupFieldErrors', () => {
  it('groups by the first path segment', () => {
    expect(
      groupFieldErrors([
        { path: 'city', message: 'a' },
        { path: '/tags/0', message: 'b' },
        { path: 'city', message: 'c' },
      ])
    ).toEqual({ city: ['a', 'c'], tags: ['b'] });
  });
});

void t;
