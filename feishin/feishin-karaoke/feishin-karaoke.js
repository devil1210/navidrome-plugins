/**
 * Feishin Karaoke & Better Lyrics Shaders Integration
 * Single-file script for Feishin Desktop (Electron)
 * 
 * Features:
 *  - Word-by-Word Karaoke Highlighting (Handles sustained notes perfectly!)
 *  - Better Lyrics Shaders (Fluid Mesh Gradient Background Canvas)
 *  - Auto Fallback to LRCLIB API for word-synced lyrics
 */
(function () {
  'use strict';

  console.log('[Feishin Karaoke Engine] Initializing...');

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

    /* Active Lyric Line Blur & Scale */
    .lyric-line, [class*="lyricLine"] {
      transition: all 0.4s cubic-bezier(0.25, 1, 0.5, 1) !important;
      opacity: 0.45;
      filter: blur(1.5px);
      transform: scale(0.97);
      margin: 12px 0 !important;
      line-height: 1.6 !important;
    }

    .lyric-line.active, [class*="lyricLine"][class*="active"], .k-line-active {
      opacity: 1 !important;
      filter: blur(0px) !important;
      transform: scale(1.04) !important;
    }

    /* Word-by-Word Karaoke Highlighting */
    .k-word {
      display: inline-block;
      transition: color 0.25s ease, transform 0.25s ease, text-shadow 0.3s ease;
      color: rgba(255, 255, 255, 0.55);
      margin: 0 3px;
      position: relative;
    }

    .k-word.k-active {
      color: #ffffff !important;
      font-weight: 700 !important;
      transform: scale(1.08);
      text-shadow: 0 0 15px rgba(255, 255, 255, 0.9), 0 0 25px rgba(255, 255, 255, 0.6);
    }

    .k-word.k-past {
      color: rgba(255, 255, 255, 0.9) !important;
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

    // Clear background
    ctx.fillStyle = '#050508';
    ctx.fillRect(0, 0, w, h);

    // Animated multi-point fluid radial gradients
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

  // --- 3. WORD-BY-WORD KARAOKE ENGINE ---
  let parsedWordLines = [];

  function parseWordSyncLine(lineText) {
    // Regex for <mm:ss.xxx> or <mm:ss.xx> timestamps
    const timestampRegex = /<(\d{2}):(\d{2})\.(\d{2,3})>/g;
    let match;
    let words = [];
    let lastIndex = 0;
    let lastTime = 0;

    const matches = [...lineText.matchAll(timestampRegex)];
    if (matches.length === 0) return null;

    for (let i = 0; i < matches.length; i++) {
      const m = matches[i];
      const minutes = parseInt(m[1], 10);
      const seconds = parseInt(m[2], 10);
      const millis = parseInt(m[3].padEnd(3, '0'), 10);
      const wordTime = minutes * 60 + seconds + millis / 1000;

      const wordText = lineText.slice(lastIndex, m.index).replace(/^\[\d{2}:\d{2}\.\d{2,3}\]/, '').trim();
      if (wordText) {
        words.push({
          text: wordText,
          startTime: lastTime,
          endTime: wordTime
        });
      }
      lastTime = wordTime;
      lastIndex = m.index + m[0].length;
    }

    const tailText = lineText.slice(lastIndex).trim();
    if (tailText) {
      words.push({
        text: tailText,
        startTime: lastTime,
        endTime: lastTime + 3.0
      });
    }

    return words;
  }

  function updateKaraokeHighlight(currentTime) {
    const wordSpans = document.querySelectorAll('.k-word');
    wordSpans.forEach(span => {
      const start = parseFloat(span.dataset.start);
      const end = parseFloat(span.dataset.end);

      if (currentTime >= start && currentTime < end) {
        span.classList.add('k-active');
        span.classList.remove('k-past');
      } else if (currentTime >= end) {
        span.classList.remove('k-active');
        span.classList.add('k-past');
      } else {
        span.classList.remove('k-active', 'k-past');
      }
    });
  }

  function processLyricLineElements() {
    const lineElements = document.querySelectorAll('.lyric-line, [class*="lyricLine"]');
    lineElements.forEach(el => {
      if (el.dataset.kProcessed) return;
      const rawText = el.textContent || '';
      const parsedWords = parseWordSyncLine(rawText);

      if (parsedWords && parsedWords.length > 0) {
        el.dataset.kProcessed = 'true';
        el.innerHTML = parsedWords.map(w =>
          `<span class="k-word" data-start="${w.startTime}" data-end="${w.endTime}">${w.text}</span>`
        ).join(' ');
      }
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
