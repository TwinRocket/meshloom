import { QRCodeSVG } from 'qrcode.react';

/** The only importer of `qrcode.react` on the enrolment path: loaded lazily, off the entry chunk. */
export default function TotpQr({ uri, title }: { uri: string; title: string }) {
  return <QRCodeSVG value={uri} size={176} level="M" title={title} />;
}
