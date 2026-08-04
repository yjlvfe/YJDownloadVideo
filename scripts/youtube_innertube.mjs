import { createWriteStream, existsSync, mkdirSync, statSync, unlinkSync } from 'node:fs';
import { join } from 'node:path';
import { Readable, Transform } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { spawnSync } from 'node:child_process';
import { BG } from 'bgutils-js';
import { JSDOM } from 'jsdom';
import { Innertube, UniversalCache } from 'youtubei.js';

const stderr = (...args) => process.stderr.write(`${args.map(String).join(' ')}\n`);
console.warn = stderr;
console.error = stderr;

const [action = 'self-test', videoId = '', requested = 'best', outputDir = '.', maxBytesRaw = '0'] = process.argv.slice(2);
const maxBytes = Math.max(0, Number(maxBytesRaw || 0));
const BOTGUARD_REQUEST_KEY = 'O43z0dpjhgX20SCx4KAo';

const value = (object, ...keys) => {
  for (const key of keys) {
    if (object && object[key] !== undefined && object[key] !== null) return object[key];
  }
  return undefined;
};

const boolValue = (object, ...keys) => Boolean(value(object, ...keys));

function mime(format) {
  return String(value(format, 'mime_type', 'mimeType') || '').toLowerCase();
}

function formatHeight(format) {
  const direct = Number(value(format, 'height') || 0);
  if (direct > 0) return direct;
  const label = String(value(format, 'quality_label', 'qualityLabel') || '');
  const match = label.match(/(\d{3,4})p/i);
  return match ? Number(match[1]) : 0;
}

function hasVideo(format) {
  return boolValue(format, 'has_video', 'hasVideo') || mime(format).startsWith('video/');
}

function allFormats(info) {
  const streaming = value(info, 'streaming_data', 'streamingData') || {};
  return [
    ...(value(streaming, 'formats') || []),
    ...(value(streaming, 'adaptive_formats', 'adaptiveFormats') || []),
  ];
}

function basicMetadata(info) {
  const basic = value(info, 'basic_info', 'basicInfo') || {};
  const thumbnails = value(basic, 'thumbnail', 'thumbnails') || [];
  const thumbnail = Array.isArray(thumbnails)
    ? String(value(thumbnails.at(-1) || {}, 'url') || '')
    : String(value(thumbnails, 'url') || '');
  return {
    title: String(value(basic, 'title') || videoId),
    duration: Number(value(basic, 'duration') || 0) || null,
    thumbnail,
  };
}

function availableHeights(info) {
  return [...new Set(
    allFormats(info)
      .filter((format) => hasVideo(format))
      .map(formatHeight)
      .filter((height) => height > 0),
  )].sort((a, b) => b - a);
}

async function writeWebStream(stream, path, byteLimit) {
  let count = 0;
  const limiter = new Transform({
    transform(chunk, _encoding, callback) {
      count += chunk.length;
      if (byteLimit > 0 && count > byteLimit) {
        callback(new Error('media exceeds configured size limit'));
        return;
      }
      callback(null, chunk);
    },
  });
  await pipeline(Readable.fromWeb(stream), limiter, createWriteStream(path));
  return count;
}

function runFfmpeg(args) {
  const result = spawnSync('ffmpeg', args, {
    encoding: 'utf8',
    stdio: ['ignore', 'ignore', 'pipe'],
    timeout: 600_000,
  });
  if (result.status !== 0) {
    throw new Error(`ffmpeg failed: ${String(result.stderr || '').slice(-500)}`);
  }
}

async function generatePoToken() {
  const bootstrap = await Innertube.create({
    cache: new UniversalCache(false),
    generate_session_locally: true,
    retrieve_player: false,
    lang: 'en',
    location: 'US',
  });
  const visitorData = bootstrap.session?.context?.client?.visitorData;
  if (!visitorData) throw new Error('Could not obtain YouTube visitor data');

  const dom = new JSDOM('<!doctype html><html><body></body></html>', {
    url: 'https://www.youtube.com/',
  });
  const previousWindow = globalThis.window;
  const previousDocument = globalThis.document;
  Object.assign(globalThis, {
    window: dom.window,
    document: dom.window.document,
  });

  try {
    const bgConfig = {
      fetch: (input, init) => fetch(input, init),
      globalObj: globalThis,
      identifier: visitorData,
      requestKey: BOTGUARD_REQUEST_KEY,
    };
    const challenge = await BG.Challenge.create(bgConfig);
    if (!challenge) throw new Error('Could not create BotGuard challenge');
    const interpreter = challenge.interpreterJavascript?.privateDoNotAccessOrElseSafeScriptWrappedValue;
    if (!interpreter) throw new Error('Could not load BotGuard interpreter');
    new Function(interpreter)();
    const tokenResult = await BG.PoToken.generate({
      program: challenge.program,
      globalName: challenge.globalName,
      bgConfig,
    });
    if (!tokenResult?.poToken) throw new Error('Could not generate YouTube PO token');
    return { visitorData, poToken: tokenResult.poToken };
  } finally {
    dom.window.close();
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
    if (previousDocument === undefined) delete globalThis.document;
    else globalThis.document = previousDocument;
  }
}

async function createClient() {
  const { visitorData, poToken } = await generatePoToken();
  return Innertube.create({
    po_token: poToken,
    visitor_data: visitorData,
    cache: new UniversalCache(false),
    generate_session_locally: true,
    retrieve_player: true,
    lang: 'en',
    location: 'US',
  });
}

async function loadInfo() {
  if (!/^[A-Za-z0-9_-]{11}$/.test(videoId)) throw new Error('invalid YouTube video id');
  const youtube = await createClient();
  const info = await youtube.getBasicInfo(videoId);
  const formats = allFormats(info);
  if (!formats.length) {
    const playability = value(info, 'playability_status', 'playabilityStatus') || {};
    throw new Error(`YouTube playability: ${String(value(playability, 'status') || 'no formats')}`);
  }
  return info;
}

function inspectPayload(info) {
  const metadata = basicMetadata(info);
  const heights = availableHeights(info);
  if (!heights.length) throw new Error('no downloadable video qualities');
  const formats = [{ id: 'best', label: 'الأفضل تلقائيًا', size: null }];
  formats.push(...heights.map((height) => ({ id: String(height), label: `${height}p`, size: null })));
  return { source: 'youtubei-js-potoken', ...metadata, formats };
}

async function downloadAudio(info, target) {
  const source = `${target}.source`;
  try {
    let stream;
    try {
      stream = await info.download({ type: 'audio', quality: 'best', format: 'mp4', codec: 'mp4a' });
    } catch {
      stream = await info.download({ type: 'audio', quality: 'best', format: 'any' });
    }
    await writeWebStream(stream, source, maxBytes);
    runFfmpeg(['-y', '-i', source, '-vn', '-c:a', 'aac', '-b:a', '192k', target]);
  } finally {
    if (existsSync(source)) unlinkSync(source);
  }
}

async function downloadVideo(info, target, height) {
  const videoSource = `${target}.video-source`;
  const audioSource = `${target}.audio-source`;
  try {
    const quality = `${height}p`;
    let videoStream;
    try {
      videoStream = await info.download({ type: 'video', quality, format: 'mp4', codec: 'avc' });
    } catch {
      videoStream = await info.download({ type: 'video', quality, format: 'any' });
    }
    let audioStream;
    try {
      audioStream = await info.download({ type: 'audio', quality: 'best', format: 'mp4', codec: 'mp4a' });
    } catch {
      audioStream = await info.download({ type: 'audio', quality: 'best', format: 'any' });
    }
    await writeWebStream(videoStream, videoSource, maxBytes);
    await writeWebStream(audioStream, audioSource, maxBytes);
    runFfmpeg([
      '-y', '-i', videoSource, '-i', audioSource,
      '-map', '0:v:0', '-map', '1:a:0',
      '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-shortest',
      '-movflags', '+faststart', target,
    ]);
  } finally {
    if (existsSync(videoSource)) unlinkSync(videoSource);
    if (existsSync(audioSource)) unlinkSync(audioSource);
  }
}

async function main() {
  if (action === 'self-test') {
    process.stdout.write(`${JSON.stringify({ ok: true, runtime: 'youtubei.js+bgutils' })}\n`);
    return;
  }
  const info = await loadInfo();
  if (action === 'inspect') {
    process.stdout.write(`${JSON.stringify(inspectPayload(info))}\n`);
    return;
  }
  if (action !== 'download') throw new Error('unsupported action');
  mkdirSync(outputDir, { recursive: true });
  const metadata = basicMetadata(info);
  const heights = availableHeights(info);
  if (requested === 'audio') {
    const target = join(outputDir, `${videoId}_youtubei_audio.m4a`);
    await downloadAudio(info, target);
    if (!existsSync(target)) throw new Error('audio output was not created');
    process.stdout.write(`${JSON.stringify({ path: target, ext: 'm4a', ...metadata, bytes: statSync(target).size })}\n`);
    return;
  }
  if (!heights.length) throw new Error('no downloadable video qualities');
  const targetHeight = requested === 'best' ? heights[0] : Number(requested);
  if (!Number.isInteger(targetHeight) || !heights.includes(targetHeight)) {
    throw new Error(`requested quality ${requested} is unavailable; available=${heights.join(',')}`);
  }
  const target = join(outputDir, `${videoId}_youtubei_${targetHeight}p.mp4`);
  await downloadVideo(info, target, targetHeight);
  if (!existsSync(target)) throw new Error('video output was not created');
  process.stdout.write(`${JSON.stringify({ path: target, ext: 'mp4', height: targetHeight, ...metadata, bytes: statSync(target).size })}\n`);
}

main().catch((error) => {
  const message = String(error?.message || error || 'unknown YouTube.js error').replace(/https?:\/\/\S+/g, '[URL]');
  process.stderr.write(`${message.slice(0, 1200)}\n`);
  process.exit(1);
});
