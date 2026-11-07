import { PASSWORD_MIN_LENGTH } from './types';

export type PasswordStrength = 'empty' | 'tooShort' | 'ok' | 'good' | 'strong';

/**
 * A hint, not a verdict: the server's policy (length, not the user name, not a common
 * password) decides. Length is what matters for a passphrase, so only length is read.
 */
export function passwordStrength(password: string): PasswordStrength {
  const length = [...password.normalize('NFC')].length;
  if (length === 0) return 'empty';
  if (length < PASSWORD_MIN_LENGTH) return 'tooShort';
  if (length < 16) return 'ok';
  if (length < 24) return 'good';
  return 'strong';
}
