'use strict';
// Settings > Writing: the owner's English and Georgian style samples (measured on save) and the Humanizer's default
// rates. Per profile: every instance saves its own (PUT /api/writing/settings).
let writingSettings=null;
function writingStats(stats){
  if(!stats)return 'No sample saved.';
  const marks=stats.per_1000_words;
  return `Measured: ${stats.sentences} sentences, ${stats.avg_sentence_words} words per sentence, ${stats.commas_per_sentence} commas per sentence, ${stats.avg_paragraph_sentences} sentences per paragraph · per 1000 words ! ${marks['!']} · ? ${marks['?']} · : ${marks[':']} · - ${marks['-']}`;
}
function renderWritingSettings(data){
  writingSettings=data;
  for(const lang of ['en','ka']){$(`writing-sample-${lang}`).value=data.samples[lang]||'';$(`writing-stats-${lang}`).textContent=writingStats(data.stats[lang]);}
  $('writing-typo-rate').value=data.typo_rate;$('writing-punct-rate').value=data.punct_rate;
  $('writing-typo-rate').max=data.limits.typo_rate;$('writing-punct-rate').max=data.limits.punct_rate;
}
async function loadWritingSettings(){renderWritingSettings(await api('/api/writing/settings'));}
$('writing-form').onsubmit=action(async event=>{
  event.preventDefault();$('writing-status').textContent='';
  const body={samples:{en:$('writing-sample-en').value,ka:$('writing-sample-ka').value},
    typo_rate:Number($('writing-typo-rate').value),punct_rate:Number($('writing-punct-rate').value)};
  renderWritingSettings(await api('/api/writing/settings',{method:'PUT',body:JSON.stringify(body)}));
  $('writing-status').textContent='Writing settings saved.';
});
const loadWriting=()=>loadWritingSettings().catch(error=>{$('writing-status').textContent=error.message;});
document.querySelector('.tabs [data-view=settings]')?.addEventListener('click',loadWriting);
$('office-settings')?.addEventListener('click',loadWriting);
