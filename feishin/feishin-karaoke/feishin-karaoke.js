/**
 * Feishin Ultra-Lightweight Karaoke Plugin
 * Simple, high-performance word-by-word lyric highlighting.
 * Zero background canvas shaders for maximum performance and 0% CPU load.
 */
(function () {
  'use strict';

  console.log('[Feishin Karaoke Lightweight] Initializing...');

  // --- 1. MINIMAL CSS STYLES ---
  const style = document.createElement('style');
  style.id = 'feishin-karaoke-styles';
  style.textContent = `
    /* Clean, high-performance word-by-word highlighting */
    .k-word {
      display: inline-block;
      transition: color 0.15s ease, font-weight 0.15s ease;
      color: rgba(255, 255, 255, 0.5);
      margin: 0 3px;
    }

    .k-word.k-active {
      color: #ffffff !important;
      font-weight: 700 !important;
      text-shadow: 0 0 12px rgba(255, 255, 255, 0.9) !important;
    }

    .k-word.k-past {
      color: rgba(255, 255, 255, 0.85) !important;
    }
  `;
  document.head.appendChild(style);

  // --- 2. UNIVERSAL WORD PARSER (Enhanced & Standard LRC) ---
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
      } else if (currentTime >= end) {
        span.classList.remove('k-active');
        span.classList.add('k-past');
      } else {
        span.classList.remove('k-active', 'k-past');
      }
    });
  }

  function processLyricLineElements() {
    const allContainers = document.querySelectorAll('[class*="lyric"], [class*="Lyric"]');
    allContainers.forEach(container => {
      const lineEls = container.querySelectorAll('p, div, span');
      let timestamps = [];

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

  // --- 3. AUDIO TIME TRACKING ---
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

  setupAudioTracking();
})();
