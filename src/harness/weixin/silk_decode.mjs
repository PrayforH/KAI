// Fixed local decoder. Never interpret message content as paths, code or URLs.
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { decode, getDuration, isSilk } = require(process.env.KAI_WEIXIN_SILK_MODULE || '/opt/kai-weixin-codec/lib/index.cjs');
try {
  const input = readFileSync(0);
  const maximum = Number(process.argv[2]);
  if (!isSilk(input) || !Number.isInteger(maximum) || maximum < 1 || maximum > 300 || getDuration(input) > maximum * 1000) process.exit(2);
  const { data, duration } = await decode(input, 16000);
  if (!data.length || duration > maximum * 1000 || data.length > maximum * 32000) process.exit(2);
  const wav = Buffer.alloc(44 + data.length);
  wav.write('RIFF', 0); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8);
  wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(16000, 24); wav.writeUInt32LE(32000, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
  wav.write('data', 36); wav.writeUInt32LE(data.length, 40); Buffer.from(data).copy(wav, 44);
  process.stdout.write(wav);
} catch { process.exit(2); }
