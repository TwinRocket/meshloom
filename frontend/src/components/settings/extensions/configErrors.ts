import { ApiError, getApiErrorCode } from '../../../api';
import { groupFieldErrors, type FieldErrors } from '../../forms/schemaFormModel';

type T = (key: string, options?: Record<string, unknown>) => string;

/**
 * The field errors of a `config_invalid` answer (`params.issues`: code and path, never a
 * value), or null for any other error. Shared by the config form of an instance and the
 * forms of the install and update dialogs.
 */
export function configIssueErrors(err: unknown, t: T): FieldErrors | null {
  const detail = err instanceof ApiError ? getApiErrorCode(err.detail) : null;
  const issues = detail?.params.issues;
  if (detail?.code !== 'config_invalid' || !Array.isArray(issues)) return null;
  return groupFieldErrors(
    issues.map((issue: { code?: string; path?: string }) => ({
      path: String(issue.path ?? ''),
      message: t(`settings.extensions.config.issue.${issue.code}`, {
        defaultValue: t('settings.extensions.config.issue.config_value_invalid'),
      }),
    }))
  );
}

/** True when a JSON Schema declares at least one property: a form is worth asking. */
export function schemaHasFields(schema: Record<string, unknown> | null | undefined): boolean {
  const properties = schema?.properties;
  return (
    typeof properties === 'object' && properties !== null && Object.keys(properties).length > 0
  );
}
