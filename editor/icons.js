// icons.js — Canvas 2D translations of the CYD TFT_eSPI icon drawing functions.
// Each function mirrors the C++ firmware logic exactly, using Canvas API calls.

const ICON_NAMES = [
  'sun', 'cloud', 'partly_cloudy', 'rain', 'heavy_rain', 'thunderstorm', 'snow', 'fog',
  'moon_full', 'moon_new', 'moon_first_quarter', 'moon_last_quarter',
  'moon_waxing_crescent', 'moon_waning_crescent', 'moon_waxing_gibbous', 'moon_waning_gibbous',
  'supermoon', 'meteor_shower', 'lunar_eclipse', 'aurora', 'iss', 'alert',
];

const ICON_LABELS = {
  sun: 'Sun', cloud: 'Cloud', partly_cloudy: 'Partly Cloudy',
  rain: 'Rain', heavy_rain: 'Heavy Rain', thunderstorm: 'Thunderstorm',
  snow: 'Snow', fog: 'Fog',
  moon_full: 'Full Moon', moon_new: 'New Moon',
  moon_first_quarter: 'First Quarter', moon_last_quarter: 'Last Quarter',
  moon_waxing_crescent: 'Waxing Crescent', moon_waning_crescent: 'Waning Crescent',
  moon_waxing_gibbous: 'Waxing Gibbous', moon_waning_gibbous: 'Waning Gibbous',
  supermoon: 'Supermoon', meteor_shower: 'Meteor Shower',
  lunar_eclipse: 'Lunar Eclipse', aurora: 'Aurora', iss: 'ISS', alert: 'Alert',
};

// ── Helpers ────────────────────────────────────────────────────────────────

function _fill(ctx, color) { ctx.fillStyle = color; }
function _stroke(ctx, color, lw = 1) { ctx.strokeStyle = color; ctx.lineWidth = lw; }

function _circle(ctx, cx, cy, r, fill) {
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  if (fill) { ctx.fillStyle = fill; ctx.fill(); }
  else ctx.stroke();
}

function _line(ctx, x1, y1, x2, y2) {
  ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
}

function _hline(ctx, x, y, len) {
  ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + len, y); ctx.stroke();
}

function _vline(ctx, x, y, len) {
  ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x, y + len); ctx.stroke();
}

function _tri(ctx, x1, y1, x2, y2, x3, y3, fill) {
  ctx.beginPath();
  ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.lineTo(x3, y3);
  ctx.closePath();
  ctx.fillStyle = fill; ctx.fill();
}

// ── Weather icons ──────────────────────────────────────────────────────────

function _drawCloud(ctx, cx, cy, r) {
  const rr = r * 5 / 9;
  ctx.fillStyle = '#c0c0c0';
  _circle(ctx, cx - rr / 2, cy, rr, '#c0c0c0');
  _circle(ctx, cx + rr / 2, cy, rr, '#c0c0c0');
  _circle(ctx, cx, cy - rr / 2, rr * 3 / 4, '#c0c0c0');
}

function _drawSun(ctx, cx, cy, r) {
  _circle(ctx, cx, cy, r * 5 / 9, '#ffff00');
  _stroke(ctx, '#ffff00', 2);
  for (let i = 0; i < 8; i++) {
    const a = i * Math.PI / 4;
    _line(ctx, cx + (r * 6 / 9) * Math.cos(a), cy + (r * 6 / 9) * Math.sin(a),
               cx + r * Math.cos(a), cy + r * Math.sin(a));
  }
}

function _drawPartlyCloudy(ctx, cx, cy, r) {
  const sx = cx + r / 4, sy = cy - r / 4, sr = r * 4 / 9;
  _circle(ctx, sx, sy, sr * 5 / 9, '#ffff00');
  _stroke(ctx, '#ffff00', 1);
  for (let i = 0; i < 8; i++) {
    const a = i * Math.PI / 4;
    _line(ctx, sx + (sr * 6 / 9) * Math.cos(a), sy + (sr * 6 / 9) * Math.sin(a),
               sx + sr * Math.cos(a), sy + sr * Math.sin(a));
  }
  const cr = r * 4 / 9, cloudX = cx - r / 6, cloudY = cy + r / 4;
  _circle(ctx, cloudX - cr / 2, cloudY, cr, '#c0c0c0');
  _circle(ctx, cloudX + cr / 2, cloudY, cr, '#c0c0c0');
  _circle(ctx, cloudX, cloudY - cr / 2, cr * 3 / 4, '#c0c0c0');
}

function _drawRain(ctx, cx, cy, r) {
  _drawCloud(ctx, cx, cy - r / 6, r);
  _stroke(ctx, '#4488ff', 1);
  for (let i = -2; i <= 2; i++) {
    const x = cx + i * r / 4;
    _line(ctx, x, cy + r / 4, x - r / 8, cy + r * 3 / 4);
  }
}

function _drawHeavyRain(ctx, cx, cy, r) {
  _drawCloud(ctx, cx, cy - r / 6, r);
  _stroke(ctx, '#4488ff', 1);
  for (let i = -3; i <= 3; i++) {
    const x = cx + i * r / 5;
    _line(ctx, x, cy + r / 5, x - r / 7, cy + r * 4 / 5);
    if (i % 2 === 0)
      _line(ctx, x + r / 8, cy + r * 2 / 5, x, cy + r * 4 / 5);
  }
}

function _drawThunderstorm(ctx, cx, cy, r) {
  _drawCloud(ctx, cx, cy - r / 6, r);
  const bx = cx, by = cy + r / 4;
  _tri(ctx, bx, by, bx - r / 5, by + r / 3, bx + r / 10, by + r / 3, '#ffff00');
  _tri(ctx, bx - r / 10, by + r / 3, bx + r / 5, by + r / 3, bx, by + r * 2 / 3, '#ffff00');
}

function _drawSnow(ctx, cx, cy, r) {
  _drawCloud(ctx, cx, cy - r / 6, r);
  _stroke(ctx, '#ffffff', 1);
  const sx = cx, sy = cy + r / 2, sr2 = r * 5 / 9;
  for (let i = 0; i < 6; i++) {
    const a = i * Math.PI / 3;
    _line(ctx, sx, sy, sx + sr2 * Math.cos(a), sy + sr2 * Math.sin(a));
  }
}

function _drawFog(ctx, cx, cy, r) {
  _stroke(ctx, '#c0c0c0', 2);
  for (let i = -2; i <= 2; i++) {
    _hline(ctx, cx - r, cy + i * r / 4, r * 2);
  }
}

// ── Moon icons ─────────────────────────────────────────────────────────────

function _drawMoon(ctx, cx, cy, r, phase) {
  _circle(ctx, cx, cy, r, '#ffffff');
  switch (phase) {
    case 'moon_new':
      _circle(ctx, cx, cy, r, '#212021');
      _stroke(ctx, '#848484', 1);
      _circle(ctx, cx, cy, r);
      break;
    case 'moon_full':
      break;
    case 'moon_first_quarter':
      ctx.fillStyle = '#000000';
      ctx.fillRect(cx - r, cy - r, r, r * 2);
      break;
    case 'moon_last_quarter':
      ctx.fillStyle = '#000000';
      ctx.fillRect(cx, cy - r, r, r * 2);
      break;
    case 'moon_waxing_crescent':
      _circle(ctx, cx - r / 3, cy, r, '#000000');
      break;
    case 'moon_waning_crescent':
      _circle(ctx, cx + r / 3, cy, r, '#000000');
      break;
    case 'moon_waxing_gibbous':
      _circle(ctx, cx + r / 4, cy, r * 3 / 4, '#000000');
      break;
    case 'moon_waning_gibbous':
      _circle(ctx, cx - r / 4, cy, r * 3 / 4, '#000000');
      break;
  }
}

// ── Astronomical icons ─────────────────────────────────────────────────────

function _drawSupermoon(ctx, cx, cy, r) {
  _stroke(ctx, '#c0c0c0', 1);
  _circle(ctx, cx, cy, r + 4);
  _circle(ctx, cx, cy, r + 2);
  _circle(ctx, cx, cy, r, '#ffffff');
}

function _drawMeteorShower(ctx, cx, cy, r) {
  ctx.fillStyle = '#000000';
  ctx.fillRect(cx - r, cy - r, r * 2, r * 2);
  _stroke(ctx, '#ffffff', 1);
  for (let i = 0; i < 5; i++) {
    const x1 = cx - r + i * r / 2 + r / 4;
    const y1 = cy - r / 2 + i * r / 4;
    _line(ctx, x1, y1, x1 + r / 3, y1 + r / 3);
  }
}

function _drawLunarEclipse(ctx, cx, cy, r) {
  _circle(ctx, cx, cy, r, '#ffffff');
  ctx.globalAlpha = 0.7;
  _circle(ctx, cx + r / 3, cy, r, '#cc2200');
  ctx.globalAlpha = 1.0;
}

function _drawAurora(ctx, cx, cy, r) {
  const colors = ['#00cc00', '#00cccc', '#00cc00', '#00cccc'];
  for (let i = 0; i < 4; i++) {
    _stroke(ctx, colors[i], 2);
    _hline(ctx, cx - r, cy - r / 2 + i * r / 3, r * 2);
    _hline(ctx, cx - r, cy - r / 2 + i * r / 3 + 1, r * 2);
  }
}

function _drawISS(ctx, cx, cy, r) {
  ctx.fillStyle = '#4488ff';
  ctx.fillRect(cx - Math.floor(r * 2 / 3), cy - Math.floor(r / 8),
               Math.floor(r * 4 / 3), Math.floor(r / 4));
  ctx.fillStyle = '#c0c0c0';
  ctx.fillRect(cx - Math.floor(r / 6), cy - Math.floor(r / 6),
               Math.floor(r / 3), Math.floor(r / 3));
  _stroke(ctx, '#7b7d7b', 1);
  _circle(ctx, cx, cy, Math.floor(r * 2 / 3));
}

function _drawAlert(ctx, cx, cy, r) {
  _tri(ctx, cx, cy - r, cx - r, cy + r, cx + r, cy + r, '#ffa400');
  ctx.fillStyle = '#000000';
  ctx.font = `bold ${Math.floor(r * 0.8)}px sans-serif`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText('!', cx, cy + Math.floor(r / 4));
}

// ── Main dispatch ──────────────────────────────────────────────────────────

function drawIcon(ctx, name, cx, cy, r) {
  if (!name) return;
  cx = Math.round(cx); cy = Math.round(cy); r = Math.round(r);
  switch (name) {
    case 'sun':            _drawSun(ctx, cx, cy, r); break;
    case 'cloud':          _drawCloud(ctx, cx, cy, r); break;
    case 'partly_cloudy':  _drawPartlyCloudy(ctx, cx, cy, r); break;
    case 'rain':           _drawRain(ctx, cx, cy, r); break;
    case 'heavy_rain':     _drawHeavyRain(ctx, cx, cy, r); break;
    case 'thunderstorm':   _drawThunderstorm(ctx, cx, cy, r); break;
    case 'snow':           _drawSnow(ctx, cx, cy, r); break;
    case 'fog':            _drawFog(ctx, cx, cy, r); break;
    case 'supermoon':      _drawSupermoon(ctx, cx, cy, r); break;
    case 'meteor_shower':  _drawMeteorShower(ctx, cx, cy, r); break;
    case 'lunar_eclipse':  _drawLunarEclipse(ctx, cx, cy, r); break;
    case 'aurora':         _drawAurora(ctx, cx, cy, r); break;
    case 'iss':            _drawISS(ctx, cx, cy, r); break;
    case 'alert':          _drawAlert(ctx, cx, cy, r); break;
    default:
      if (name.startsWith('moon_')) _drawMoon(ctx, cx, cy, r, name);
  }
}

// Convenience: render an icon centered on its own canvas element.
function renderIconToCanvas(canvas, iconName, r) {
  const ctx = canvas.getContext('2d');
  const cx = canvas.width / 2, cy = canvas.height / 2;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#000000';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  drawIcon(ctx, iconName, cx, cy, r || Math.floor(Math.min(canvas.width, canvas.height) * 0.42));
}
