/**
 * Client-side preparation of a photo before `POST /api/share/files` (MSHL-245).
 *
 * The browser decodes the image, scales it so its longest side is at most
 * `MAX_SIDE` and re-encodes it: that is what keeps the upload small on a mobile
 * link and what strips the EXIF block (location, camera, time). Native APIs only
 * (createImageBitmap, canvas): no WebCrypto, so it works on plain HTTP. A GIF is
 * sent untouched (re-encoding would flatten the animation) and only checked
 * against its cap.
 *
 * The size and type decisions are pure functions so they are unit-testable
 * without a canvas; `downscaleImage` is the thin DOM part around them.
 */

/** Longest side of an uploaded image, in pixels. */
export const MAX_SIDE = 1600;
export const JPEG_QUALITY = 0.82;
/** Caps of the share service (contract "Files API v1"): a stored image, a GIF. */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
export const MAX_GIF_BYTES = 5 * 1024 * 1024;
/** A source larger than this is refused before decoding (memory on a Pi kiosk). */
export const MAX_SOURCE_BYTES = 40 * 1024 * 1024;

/** MIME types the service stores, and the extensions the file picker offers for them. */
export const IMAGE_ACCEPT: Record<string, string[]> = {
  'image/jpeg': ['.jpg', '.jpeg'],
  'image/png': ['.png'],
  'image/webp': ['.webp'],
  'image/gif': ['.gif'],
};

export type OutputType = 'image/jpeg' | 'image/png' | 'image/webp' | 'image/gif';

/** Why a file cannot be sent. Each code has a `chat.fileTab.error.<code>` message. */
export type ImageRefusal = 'heic' | 'unsupported' | 'tooLarge' | 'decode';

export class ImageRefusedError extends Error {
  constructor(public readonly code: ImageRefusal) {
    super(code);
    this.name = 'ImageRefusedError';
  }
}

export interface Dimensions {
  width: number;
  height: number;
}

/** Size after scaling the longest side down to `max` (never up), rounded, at least 1 px. */
export function fitWithin({ width, height }: Dimensions, max: number = MAX_SIDE): Dimensions {
  const longest = Math.max(width, height);
  if (!(longest > max)) return { width, height };
  const ratio = max / longest;
  return {
    width: Math.max(1, Math.round(width * ratio)),
    height: Math.max(1, Math.round(height * ratio)),
  };
}

const HEIC_TYPES = new Set([
  'image/heic',
  'image/heif',
  'image/heic-sequence',
  'image/heif-sequence',
]);
const HEIC_EXTENSION = /\.(heic|heif)$/i;

/** The file is an iPhone HEIC/HEIF photo (the type is often empty on desktop browsers). */
export function isHeic(file: { type: string; name: string }): boolean {
  return HEIC_TYPES.has(file.type.toLowerCase()) || HEIC_EXTENSION.test(file.name);
}

const SOURCE_EXTENSIONS: Record<string, OutputType> = {
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
  webp: 'image/webp',
  gif: 'image/gif',
};

/**
 * The type of a source file, or the reason it is refused. The MIME type decides;
 * the extension only fills in an empty one (some systems give none).
 */
export function classifySource(file: {
  type: string;
  name: string;
}): OutputType | 'heic' | 'unsupported' {
  if (isHeic(file)) return 'heic';
  const mime = file.type.toLowerCase();
  if (mime === 'image/jpg') return 'image/jpeg';
  if (mime in IMAGE_ACCEPT) return mime as OutputType;
  if (mime === '') {
    const extension = /\.([a-z0-9]+)$/i.exec(file.name)?.[1]?.toLowerCase();
    if (extension && extension in SOURCE_EXTENSIONS) return SOURCE_EXTENSIONS[extension];
  }
  return 'unsupported';
}

/**
 * The type to encode to. GIF stays GIF (untouched); PNG with transparency stays
 * PNG; WebP stays WebP when this browser encodes it; everything else is JPEG.
 * A source with transparency never falls back to JPEG (it would turn black).
 */
export function chooseOutputType(
  source: OutputType,
  hasAlpha: boolean,
  canEncodeWebp: boolean
): OutputType {
  if (source === 'image/gif') return 'image/gif';
  if (source === 'image/png' && hasAlpha) return 'image/png';
  if (source === 'image/webp') {
    if (canEncodeWebp) return 'image/webp';
    return hasAlpha ? 'image/png' : 'image/jpeg';
  }
  return 'image/jpeg';
}

/** The cap on the bytes sent, by type. */
export function maxBytesFor(type: OutputType): number {
  return type === 'image/gif' ? MAX_GIF_BYTES : MAX_UPLOAD_BYTES;
}

export interface PreparedImage {
  blob: Blob;
  type: OutputType;
  /** Pixels of the source and of what is sent (equal for an untouched GIF). */
  original: Dimensions;
  output: Dimensions;
  originalBytes: number;
  bytes: number;
}

interface Decoded {
  source: CanvasImageSource;
  width: number;
  height: number;
  close: () => void;
}

async function decode(file: File): Promise<Decoded> {
  if (typeof createImageBitmap === 'function') {
    try {
      const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
      return {
        source: bitmap,
        width: bitmap.width,
        height: bitmap.height,
        close: () => bitmap.close(),
      };
    } catch {
      // Fall back to an <img>: some browsers reject the options bag or a format.
    }
  }
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    img.src = url;
    await img.decode();
    // <img> applies the EXIF orientation on its own in current browsers.
    return {
      source: img,
      width: img.naturalWidth,
      height: img.naturalHeight,
      close: () => URL.revokeObjectURL(url),
    };
  } catch {
    URL.revokeObjectURL(url);
    throw new ImageRefusedError(isHeic(file) ? 'heic' : 'decode');
  }
}

function toBlob(canvas: HTMLCanvasElement, type: string, quality?: number): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, type, quality));
}

function sampleHasAlpha(ctx: CanvasRenderingContext2D, { width, height }: Dimensions): boolean {
  const { data } = ctx.getImageData(0, 0, width, height);
  for (let i = 3; i < data.length; i += 4) {
    if (data[i] < 255) return true;
  }
  return false;
}

let webpEncodable: Promise<boolean> | null = null;

/** Whether `canvas.toBlob('image/webp')` really produces WebP here (Safari answers PNG). */
function canEncodeWebp(): Promise<boolean> {
  webpEncodable ??= (async () => {
    try {
      const canvas = document.createElement('canvas');
      canvas.width = canvas.height = 1;
      const blob = await toBlob(canvas, 'image/webp', 0.8);
      return blob?.type === 'image/webp';
    } catch {
      return false;
    }
  })();
  return webpEncodable;
}

/**
 * Decode, scale and re-encode `file`. Throws `ImageRefusedError` (never a bare
 * message) when the file cannot be sent: HEIC, another type, undecodable, or
 * still over the service's cap after encoding.
 */
export async function downscaleImage(file: File): Promise<PreparedImage> {
  const kind = classifySource(file);
  if (kind === 'heic' || kind === 'unsupported') throw new ImageRefusedError(kind);
  if (file.size > MAX_SOURCE_BYTES) throw new ImageRefusedError('tooLarge');
  // The GIF cap is known from the size alone: refuse before decoding.
  if (kind === 'image/gif' && file.size > MAX_GIF_BYTES) throw new ImageRefusedError('tooLarge');
  const decoded = await decode(file);
  try {
    const original = { width: decoded.width, height: decoded.height };
    if (!(original.width > 0 && original.height > 0)) throw new ImageRefusedError('decode');
    if (kind === 'image/gif') {
      const blob = file.type === 'image/gif' ? file : new Blob([file], { type: 'image/gif' });
      return {
        blob,
        type: 'image/gif',
        original,
        output: original,
        originalBytes: file.size,
        bytes: file.size,
      };
    }
    const output = fitWithin(original);
    const canvas = document.createElement('canvas');
    canvas.width = output.width;
    canvas.height = output.height;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    if (!ctx) throw new ImageRefusedError('decode');
    ctx.drawImage(decoded.source, 0, 0, output.width, output.height);
    // Transparency only matters for the types that can carry it.
    const hasAlpha = (kind === 'image/png' || kind === 'image/webp') && sampleHasAlpha(ctx, output);
    const type = chooseOutputType(kind, hasAlpha, kind === 'image/webp' && (await canEncodeWebp()));
    const blob = await toBlob(canvas, type, type === 'image/png' ? undefined : JPEG_QUALITY);
    if (!blob || blob.type !== type) throw new ImageRefusedError('decode');
    if (blob.size > maxBytesFor(type)) throw new ImageRefusedError('tooLarge');
    return { blob, type, original, output, originalBytes: file.size, bytes: blob.size };
  } finally {
    decoded.close();
  }
}
