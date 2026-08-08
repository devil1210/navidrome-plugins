/**
 * Feishin Ultra-Lightweight Karaoke Plugin
 * Intercepts Subsonic API lyrics requests to prevent Feishin React crashes
 * while providing high-precision word-by-word lyric highlighting.
 */
(function () {
  'use strict';

  console.log('[Feishin Karaoke] Initializing interceptor & word highlighter...');

  window.__last_raw_lyrics = '';

  // --- 1. MINIMAL CSS STYLES ---
  if (!document.getElementById('feishin-karaoke-styles')) {
    const style = document.createElement('style');
    style.id = 'feishin-karaoke-styles';
    style.textContent = `
      .k-word {
        display: inline !important;
        white-space: pre-wrap !important;
        background: transparent !important;
        background-image: none !important;
        -webkit-background-clip: border-box !important;
        background-clip: border-box !important;
        -webkit-text-fill-color: currentcolor !important;
        color: rgba(255, 255, 255, 0.45) !important;
        margin: 0 1px !important;
        transition: color 0.12s ease, font-weight 0.12s ease, text-shadow 0.12s ease, transform 0.12s ease !important;
      }

      .k-word.k-active {
        color: #ffffff !important;
        font-weight: 700 !important;
        text-shadow: 0 0 14px rgba(255, 255, 255, 0.95), 0 0 24px rgba(255, 255, 255, 0.6) !important;
        transform: scale(1.04) !important;
      }

      .k-word.k-past {
        color: rgba(255, 255, 255, 0.88) !important;
      }
    `;
    document.head.appendChild(style);
  }

  // --- 2. FETCH INTERCEPTOR (Prevents Feishin React Crashes while storing raw word timestamps) ---
  const origFetch = window.fetch;
  window.fetch = async function (...args) {
    const res = await origFetch.apply(this, args);
    const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url ? args[0].url : '');

    if (url.includes('getLyrics') || url.includes('lyrics') || url.includes('/api/v1/')) {
      try {
        const clone = res.clone();
        const text = await clone.text();

        if (text.includes('<') && text.includes('>')) {
          // Store original raw lyrics with <mm:ss.xx> for word-level karaoke parsing
          window.__last_raw_lyrics = text;

          // Strip <mm:ss.xx> tags for Feishin React component so React never crashes on XML/HTML angle brackets
          const modifiedText = text.replace(/<(\d{2}):(\d{2})\.(\d{2,3})>/g, '');

          return new Response(modifiedText, {
            status: res.status,
            statusText: res.statusText,
            headers: res.headers
          });
        }
      } catch (e) {
        console.warn('[Feishin Karaoke] Fetch intercept error:', e);
      }
    }
    return res;
  };

  // --- 3. WORD PARSER & HIGHLIGHTER ---
  function parseWordSyncLine(lineText, startTime, endTime, rawLineText) {
    const sourceText = (rawLineText && rawLineText.includes('<')) ? rawLineText : lineText;
    const timestampRegex = /<(\d{2}):(\d{2})\.(\d{2,3})>/;

    // Word-sync parsing with <mm:ss.xx> tags
    if (sourceText.includes('<')) {
      const lineNoHeader = sourceText.replace(/^\[\d{2}:\d{2}\.\d{2,3}\]/, '').trim();
      const rawTokens = lineNoHeader.split(/\s+/);

      let words = [];
      let lineDefaultTime = startTime || 0;

      for (let i = 0; i < rawTokens.length; i++) {
        const tok = rawTokens[i];
        if (!tok.trim()) continue;

        const m = tok.match(timestampRegex);
        let t = null;
        if (m) {
          t = parseInt(m[1], 10) * 60 + parseInt(m[2], 10) + parseInt(m[3].padEnd(3, '0'), 10) / 1000;
        }

        // Clean all inner timestamp tags to preserve the complete whole word
        const cleanWord = tok.replace(/<(\d{2}):(\d{2})\.(\d{2,3})>/g, '').trim();
        if (!cleanWord) continue;

        const wStart = (t !== null) ? t : lineDefaultTime;

        words.push({
          text: cleanWord,
          startTime: wStart,
          endTime: wStart + 1.0
        });

        lineDefaultTime = wStart;
      }

      if (words.length > 0) {
        for (let i = 0; i < words.length - 1; i++) {
          if (words[i + 1].startTime > words[i].startTime) {
            words[i].endTime = words[i + 1].startTime;
          } else {
            words[i].endTime = words[i].startTime + 0.5;
          }
        }
        const lastWord = words[words.length - 1];
        lastWord.endTime = (endTime && endTime > lastWord.startTime) ? endTime : lastWord.startTime + 3.0;
        return words;
      }
    }

    // Fallback for line-level sync without <mm:ss.xx> tags
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
    const rawLyrics = window.__last_raw_lyrics || '';
    const rawLines = rawLyrics ? rawLyrics.split('\n') : [];

    allContainers.forEach(container => {
      // Exclude mini-player, cover art overlays, or tiny sidebar widgets
      if (container.closest('[class*="mini"]') || container.closest('[class*="cover"]') || container.closest('[class*="sidebar"]') || container.offsetWidth < 280) {
        return;
      }

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

        // Match raw line containing <mm:ss.xx> timestamps
        const rawLine = rawLines.find(rl => {
          const cleanRl = rl.replace(/<(\d{2}):(\d{2})\.(\d{2,3})>/g, '').replace(/^\[\d{2}:\d{2}\.\d{2,3}\]/, '').trim();
          const cleanText = text.replace(/^\[\d{2}:\d{2}\.\d{2,3}\]/, '').trim();
          return cleanRl && cleanText && (cleanRl === cleanText || cleanRl.includes(cleanText));
        }) || '';

        const words = parseWordSyncLine(text, startTime, endTime, rawLine);
        if (words && words.length > 0) {
          el.dataset.kProcessed = 'true';
          el.innerHTML = words.map(w =>
            `<span class="k-word" data-start="${w.startTime}" data-end="${w.endTime}">${w.text}</span>`
          ).join(' ');
        }
      });
    });
  }

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
