import {html, useState, useEffect, useRef} from '../vendor/preact-htm.module.js';

export const Seg = ({tabs, value, onPick, start}) => html`
  <div class=${`seg${start ? ' start' : ''}`} role="tablist">
    ${tabs.map(([key, label, count]) => html`<button type="button" role="tab" aria-selected=${value === key}
      class=${value === key ? 'on' : ''} onClick=${() => onPick(key)}>${label}${count ? html`<span class="c">${count}</span>` : ''}</button>`)}
  </div>`;

export const Btn = ({label, onClick, kind = '', k, size = '', title, disabled}) => html`
  <button type="button" class=${`btn ${kind} ${size}`} title=${title || ''} disabled=${disabled}
    onClick=${e => { e.stopPropagation(); onClick && onClick(e); }}>${k ? html`<span class="key">${k}</span>` : ''}${label}</button>`;

export function Toast({toast, onUndo}) {
  if (!toast) return null;
  return html`<div class=${`toast${toast.error ? ' err' : ''}`} role="status"><span>${toast.text}</span>
    ${toast.undo ? html`<button type="button" onClick=${onUndo}>Undo</button>` : ''}</div>`;
}

// A dialog is {title, hint, fields:[{key,label,type,options,value,mono,optional,rows}], save, onSubmit(form)}.
export function Dialog({spec, onClose}) {
  const [form, setForm] = useState(() => Object.fromEntries(spec.fields.map(f => [f.key,
    f.value !== undefined ? f.value : f.type === 'select' ? ((f.options[0] || {}).value ?? '') : f.type === 'check' ? false : ''])));
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const first = useRef(null);
  useEffect(() => { if (first.current) first.current.focus(); }, []);
  const set = (key, value) => { setForm(prev => ({...prev, [key]: value})); setError(''); };
  async function submit(e) {
    if (e) e.preventDefault();
    if (busy) return;
    for (const f of spec.fields) {
      if (!f.optional && f.type !== 'readonly' && f.type !== 'check' && !String(form[f.key] ?? '').trim()) {
        setError(`Fill in “${f.label}”.`); return;
      }
    }
    setBusy(true);
    try { if (spec.onSubmit) await spec.onSubmit(form); onClose(); }
    catch (err) { setError(err.message || String(err)); }
    finally { setBusy(false); }
  }
  const onKey = e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submit(e); if (e.key === 'Escape') onClose(); };
  let firstSet = false;
  const ref = () => { if (firstSet) return undefined; firstSet = true; return first; };
  return html`<div class="scrim center" onClick=${onClose}>
    <form class="dialog" role="dialog" aria-modal="true" aria-label=${spec.title} onClick=${e => e.stopPropagation()} onSubmit=${submit} onKeyDown=${onKey}>
      <div class="hd"><h2>${spec.title}</h2><button type="button" class="close" aria-label="Close" onClick=${onClose}>✕</button></div>
      ${spec.hint ? html`<div class="hint">${spec.hint}</div>` : ''}
      ${spec.body || ''}
      ${spec.fields.map(f => {
        if (f.type === 'readonly') return html`<label class="f">${f.label}<div class="ro">${f.value}</div></label>`;
        if (f.type === 'check') return html`<label class="check"><input type="checkbox" checked=${!!form[f.key]} onChange=${e => set(f.key, e.target.checked)} />${f.label}</label>`;
        if (f.type === 'select') return html`<label class="f">${f.label}<select ref=${ref()} value=${form[f.key]} onChange=${e => set(f.key, e.target.value)}>
          ${f.options.map(o => html`<option value=${o.value} disabled=${o.disabled}>${o.label}</option>`)}</select></label>`;
        if (f.type === 'area') return html`<label class="f">${f.label}<textarea ref=${ref()} class=${f.mono ? 'mono' : ''} rows=${f.rows || 4}
          value=${form[f.key]} onInput=${e => set(f.key, e.target.value)}></textarea></label>`;
        return html`<label class="f">${f.label}<input ref=${ref()} class=${f.mono ? 'mono' : ''} value=${form[f.key]} onInput=${e => set(f.key, e.target.value)} /></label>`;
      })}
      ${error ? html`<div class="err" role="alert">${error}</div>` : ''}
      <div class="ft">
        ${spec.save === null ? '' : html`<button type="button" class="btn" onClick=${onClose}>Cancel</button>`}
        <button type="submit" class="btn p" disabled=${busy}>${busy ? 'Saving…' : (spec.save || 'Save')}</button>
      </div>
    </form></div>`;
}
