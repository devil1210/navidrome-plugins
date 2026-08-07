/**
 * Feishin Karaoke & Better Lyrics Shaders Integration
 * Single-file script for Feishin Desktop (Electron)
 * 
 * Features:
 *  - Universal Word-by-Word Karaoke Highlighting (Enhanced & Standard LRC)
 *  - Crisp Typography with Active Line Resplandor (Fixes blur/nublado)
 *  - Better Lyrics Shaders (Fluid Mesh Gradient Background Canvas)
 */
(function () {
  'use strict';

  console.log('[Feishin Karaoke Engine v2] Initializing...');

  // --- 1. CSS STYLES INJECTION ---
  const style = document.createElement('style');
  style.id = 'feishin-karaoke-styles';
  style.textContent = `
    /* Fluid Shader Canvas Background */
    #feishin-shader-canvas {
      position: fixed;
      top: 0;
      left: 0;
      width: 100vw;
      height: 100vh;
      z-index: -1;
      pointer-events: none;
      filter: blur(40px) brightness(0.85);
      transition: opacity 1s ease;
      opacity: 0.85;
    }

    /* Crisp Lyric Lines (Fixes nublado/blur issue) */
    p, span, div {
      transition: color 0.3s ease, opacity 0.3s ease, filter 0.3s ease, transform 0.3s ease;
    }

    .k-line-inactive {
      opacity: 0.45 !important;
      filter: blur(0.6px) !important;
      transform: scale(0.97) !important;
    }

    .k-line-active, .lyric-line.active, [class*="active"] {
      opacity: 1 !important;
      filter: blur(0px) !important;
      transform: scale(1.04) !important;
      color: #ffffff !important;
      font-weight: 700 !important;
      text-shadow: 0 0 15px rgba(255, 255, 255, 0.8), 0 0 25px rgba(255, 255, 255, 0.5) !important;
    }

    /* Word-by-Word Karaoke Highlighting */
    .k-word {
      display: inline-block;
      transition: color 0.2s ease, transform 0.2s ease, text-shadow 0.2s ease;
      color: rgba(255, 255, 255, 0.6);
      margin: 0 3px;
      position: relative;
    }

    .k-word.k-active {
      color: #ffffff !important;
      font-weight: 700 !important;
      transform: scale(1.08);
      text-shadow: 0 0 18px rgba(255, 255, 255, 0.95), 0 0 28px rgba(255, 255, 255, 0.7);
    }

    .k-word.k-past {
      color: rgba(255, 255, 255, 0.92) !important;
    }
  `;
  document.head.appendChild(style);

  // --- 2. FLUID SHADER CANVAS (Better Lyrics Shaders) ---
  let canvas, ctx;
  let animFrameId;
  let color1 = { r: 60, g: 20, b: 90 };
  let color2 = { r: 20, g: 80, b: 120 };
  let color3 = { r: 120, g: 40, b: 70 };
  let time = 0;

  function initCanvas() {
    if (document.getElementById('feishin-shader-canvas')) return;
    canvas = document.createElement('canvas');
    canvas.id = 'feishin-shader-canvas';
    document.body.prepend(canvas);
    ctx = canvas.getContext('2d');
    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);
    renderShader();
  }

  function resizeCanvas() {
    if (!canvas) return;
    canvas.width = window.innerWidth / 2;
    canvas.height = window.innerHeight / 2;
  }

  function renderShader() {
    if (!ctx || !canvas) return;
    time += 0.008;

    const w = canvas.width;
    const h = canvas.height;

    ctx.fillStyle = '#050508';
    ctx.fillRect(0, 0, w, h);

    const x1 = w * (0.5 + 0.3 * Math.sin(time * 0.7));
    const y1 = h * (0.5 + 0.3 * Math.cos(time * 0.5));
    const g1 = ctx.createRadialGradient(x1, y1, 10, x1, y1, w * 0.7);
    g1.addColorStop(0, `rgba(${color1.r}, ${color1.g}, ${color1.b}, 0.8)`);
    g1.addColorStop(1, 'transparent');

    const x2 = w * (0.5 + 0.35 * Math.cos(time * 0.6));
    const y2 = h * (0.5 + 0.3 * Math.sin(time * 0.8));
    const g2 = ctx.createRadialGradient(x2, y2, 10, x2, y2, w * 0.7);
    g2.addColorStop(0, `rgba(${color2.r}, ${color2.g}, ${color2.b}, 0.8)`);
    g2.addColorStop(1, 'transparent');

    const x3 = w * (0.5 + 0.25 * Math.sin(time * 0.9));
    const y3 = h * (0.5 + 0.35 * Math.cos(time * 0.4));
    const g3 = ctx.createRadialGradient(x3, y3, 10, x3, y3, w * 0.6);
    g3.addColorStop(0, `rgba(${color3.r}, ${color3.g}, ${color3.b}, 0.7)`);
    g3.addColorStop(1, 'transparent');

    ctx.fillStyle = g1; ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = g2; ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = g3; ctx.fillRect(0, 0, w, h);

    animFrameId = requestAnimationFrame(renderShader);
  }

  // --- 3. UNIVERSAL WORD-BY-WORD KARAOKE ENGINE ---
  function parseWordSyncLine(lineText, startTime, endTime) {
    const timestampRegex = /<(\d{2}):(\d{2})\.(\d{2,3})>/g;
    const matches = [...lineText.matchAll(timestampRegex)];

    // Explicit word timestamps <mm:ss.xx>
    if (matches.length > 0) {
      let words = [];
      let lastIndex = 0;
      let lastTime = startTime || 0;

      for (let i = 0; i < matches.length; i++) {
        const m = matches[i];
        const wordTime = parseInt(m[1], 10) * 60 + parseInt(m[2], 10) + parseInt(m[3].padEnd(3, '0'), 10) / 1000;
        const wordText = lineText.slice(lastIndex, m.index).replace(/^\[\d{2}:\d{2}\.\d{2,3}\]/, '').trim();
        if (wordText) {
          words.push({ text: wordText, startTime: lastTime, endTime: wordTime });
        }
        lastTime = wordTime;
        lastIndex = m.index + m[0].length;
      }
      const tailText = lineText.slice(lastIndex).trim();
      if (tailText) {
        words.push({ text: tailText, startTime: lastTime, endTime: lastTime + 3.0 });
      }
      return words;
    }

    // Standard LRC Line -> Interpolate word timestamps evenly across line duration
    const cleanText = lineText.replace(/^\[\d{2}:\d{2}\.\d{2,3}\]/, '').trim();
    if (!cleanText) return null;

    const rawWords = cleanText.split(/\s+/);
    if (rawWords.length === 0) return null;

    const dur = (endTime && endTime > startTime) ? (endTime - startTime) : 4.0;
    const step = dur / rawWords.length;

    return rawWords.map((w, idx) => ({
      text: w,
      startTime: startTime + (idx * step),
      endTime: startTime + ((idx + 1) * step)
    }));
  }

  function updateKaraokeHighlight(currentTime) {
    const wordSpans = document.querySelectorAll('.k-word');
    wordSpans.forEach(span => {
      const start = parseFloat(span.dataset.start);
      const end = parseFloat(span.dataset.end);

      if (currentTime >= start && currentTime < end) {
        span.classList.add('k-active');
        span.classList.remove('k-past');

        // Ensure parent line is active & unblurred
        const parentLine = span.closest('p, div, span');
        if (parentLine) {
          parentLine.classList.add('k-line-active');
          parentLine.classList.remove('k-line-inactive');
        }
      } else if (currentTime >= end) {
        span.classList.remove('k-active');
        span.classList.add('k-past');
      } else {
        span.classList.remove('k-active', 'k-past');
      }
    });
  }

  function processLyricLineElements() {
    // Find all text elements in lyrics container
    const allContainers = document.querySelectorAll('[class*="lyric"], [class*="Lyric"]');
    allContainers.forEach(container => {
      const lineEls = container.querySelectorAll('p, div, span');
      let timestamps = [];

      // Extract line timestamps if present
      lineEls.forEach((el, i) => {
        const text = el.textContent || '';
        const m = text.match(/\[(\d{2}):(\d{2})\.(\d{2,3})\]/);
        if (m) {
          const t = parseInt(m[1], 10) * 60 + parseInt(m[2], 10) + parseInt(m[3].padEnd(3, '0'), 10) / 1000;
          el.dataset.kStartTime = t;
          timestamps.push({ idx: i, time: t });
        }
      });

      lineEls.forEach((el, i) => {
        if (el.dataset.kProcessed || el.children.length > 2) return;
        const text = el.textContent || '';
        if (!text.trim()) return;

        const startTime = parseFloat(el.dataset.kStartTime) || 0;
        let endTime = startTime + 4.0;

        // Find next line timestamp for duration
        const nextTs = timestamps.find(ts => ts.idx > i);
        if (nextTs) endTime = nextTs.time;

        const words = parseWordSyncLine(text, startTime, endTime);
        if (words && words.length > 0) {
          el.dataset.kProcessed = 'true';
          el.innerHTML = words.map(w =>
            `<span class="k-word" data-start="${w.startTime}" data-end="${w.endTime}">${w.text}</span>`
          ).join(' ');
        }
      });
    });
  }

  // --- 4. AUDIO TIME TRACKING & DOM OBSERVER ---
  function setupAudioTracking() {
    setInterval(() => {
      const audio = document.querySelector('audio');
      if (audio && !audio.dataset.kTracked) {
        audio.dataset.kTracked = 'true';
        audio.addEventListener('timeupdate', () => {
          updateKaraokeHighlight(audio.currentTime);
        });
      }
      processLyricLineElements();
    }, 300);
  }

  // Initialization
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      initCanvas();
      setupAudioTracking();
    });
  } else {
    initCanvas();
    setupAudioTracking();
  }

})();
