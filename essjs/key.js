// Reads mono float32 LE audio at 44.1 kHz on stdin, prints {"key","scale","strength"} as JSON.
const { Essentia, EssentiaWASM } = require('essentia.js');
const chunks = [];
process.stdin.on('data', c => chunks.push(c));
process.stdin.on('end', () => {
  const b = Buffer.concat(chunks);
  const a = new Float32Array(b.buffer.slice(b.byteOffset, b.byteOffset + (b.length & ~3)));
  const ess = new Essentia(EssentiaWASM);
  const v = ess.arrayToVector(a);
  const r = ess.KeyExtractor(v, true, 4096, 4096, 12, 3500, 60, 25, 0.2, 'bgate', 44100, 0.0001, 440, 'cosine', 'hann');
  v.delete();
  console.log(JSON.stringify({ key: r.key, scale: r.scale, strength: r.strength }));
});
