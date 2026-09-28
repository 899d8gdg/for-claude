// Paste into the browser DevTools console (F12 → Console) on the page whose
// PNGs you want. It scrolls the page so lazy images load, collects every PNG
// the page requested (same list as Network → Img), and downloads each one.
(async () => {
  for (let y = 0; y < document.body.scrollHeight; y += 600) {
    window.scrollTo(0, y);
    await new Promise((r) => setTimeout(r, 400));
  }
  window.scrollTo(0, 0);
  await new Promise((r) => setTimeout(r, 2000));

  const found = new Set();
  const add = (u) => {
    try {
      const abs = new URL(u, location.href);
      if (/^https?:$/.test(abs.protocol) && /\.png$/i.test(abs.pathname)) found.add(abs.href);
    } catch {}
  };
  performance.getEntriesByType('resource').forEach((e) => add(e.name));
  document.querySelectorAll('img, source').forEach((el) => {
    add(el.currentSrc || el.src || '');
    (el.srcset || '').split(',').forEach((c) => add(c.trim().split(/\s+/)[0]));
  });
  document.querySelectorAll('*').forEach((el) => {
    const bg = getComputedStyle(el).backgroundImage;
    for (const m of bg.matchAll(/url\(["']?([^"')]+)["']?\)/g)) add(m[1]);
  });

  const urls = [...found];
  console.log(`Found ${urls.length} PNG(s)`);
  const names = new Set();
  const failed = [];
  for (const u of urls) {
    let name = decodeURIComponent(new URL(u).pathname.split('/').pop()) || 'image.png';
    for (let i = 1; names.has(name); i++) name = name.replace(/(-\d+)?\.png$/i, `-${i}.png`);
    names.add(name);
    try {
      const res = await fetch(u);
      if (!res.ok) throw new Error(res.status);
      const a = document.createElement('a');
      a.href = URL.createObjectURL(await res.blob());
      a.download = name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
      await new Promise((r) => setTimeout(r, 300));
    } catch {
      failed.push(u); // usually a CDN that blocks cross-origin fetches
    }
  }
  console.log(`Downloaded ${urls.length - failed.length} PNG(s).`);
  if (failed.length) {
    console.log(`${failed.length} could not be fetched from the page; open or save these directly:\n` + failed.join('\n'));
  }
  if (typeof copy === 'function') copy(urls.join('\n')); // DevTools helper: puts the full list on the clipboard
})();
