import { Globe } from 'lucide-react';
import { useTranslation } from 'react-i18next';

export const DIRECTORY_GLOBE_COLOR = '#f97316';

/** Orange globe SVG for CSS2D labels (same mark as DirectoryGlobeIcon). */
export function createDirectoryGlobeElement(): SVGSVGElement {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', '11');
  svg.setAttribute('height', '11');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', DIRECTORY_GLOBE_COLOR);
  svg.setAttribute('stroke-width', '2');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.style.display = 'none';
  svg.style.flexShrink = '0';

  const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  circle.setAttribute('cx', '12');
  circle.setAttribute('cy', '12');
  circle.setAttribute('r', '10');
  const meridians = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  meridians.setAttribute('d', 'M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20');
  const equator = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  equator.setAttribute('d', 'M2 12h20');
  svg.append(circle, meridians, equator);
  return svg;
}

/** Orange globe for a directory hop name when local resolve is not known. */
export function DirectoryGlobeIcon() {
  const { t } = useTranslation();
  return (
    <Globe
      aria-label={t('path.directoryGlobe')}
      data-testid="directory-globe-icon"
      className="size-3.5 shrink-0 text-orange-500"
    />
  );
}
