import type { ServerLabel } from '../types';
import { getContrastTextColor } from '../utils/localLabel';
import { serverLabelStyle, serverLabelVisible } from '../utils/serverLabel';

/**
 * Says which Meshloom this is, for someone who runs more than one.
 *
 * Three instances look identical, and the address bar is the only thing telling
 * them apart on a phone that hides it. Absent by default: an instance nobody
 * needs to distinguish should not lose a strip of screen to the option.
 */
export function ServerLabelBand({ label }: { label: ServerLabel }) {
  if (!serverLabelVisible(label)) return null;

  const style = serverLabelStyle(label);
  return (
    <div
      data-testid="server-label-band"
      role="note"
      style={{
        ...style,
        backgroundColor: label.color,
        color: getContrastTextColor(label.color),
      }}
      className="truncate text-center leading-tight"
    >
      {label.text}
    </div>
  );
}
