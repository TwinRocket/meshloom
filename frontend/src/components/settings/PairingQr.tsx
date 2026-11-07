import { QRCodeSVG } from 'qrcode.react';

/** The only importer of `qrcode.react` on the pairing path: loaded lazily, off the entry chunk. */
export default function PairingQr({ value, title }: { value: string; title: string }) {
  return (
    <QRCodeSVG value={value} size={224} level="M" title={title} className="h-auto max-w-full" />
  );
}
