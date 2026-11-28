'use strict';
// Settings > Writing: the owner's English and Georgian style samples (measured on save) and the Humanizer's default
// rates. Per profile: every instance saves its own (PUT /api/writing/settings).
// On an instance (setupHumanizerSheet) the panel moves out of Settings into the chat menu's Humanizer sheet.
const tw=text=>typeof tr==='function'?tr(text):text;
let writingSettings=null,humanizerSheet=false;
function writingStats(stats){
  if(!stats)return tw('No sample saved.');
  const marks=stats.per_1000_words;
  return `${tw('Measured')}: ${stats.sentences} ${tw('sentences')}, ${stats.avg_sentence_words} ${tw('words per sentence')}, ${stats.commas_per_sentence} ${tw('commas per sentence')}, ${stats.avg_paragraph_sentences} ${tw('sentences per paragraph')} · ${tw('per 1000 words')} ! ${marks['!']} · ? ${marks['?']} · : ${marks[':']} · - ${marks['-']}`;
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
  $('writing-status').textContent=tw(humanizerSheet?'Humanizer settings saved.':'Writing settings saved.');
});
const loadWriting=()=>loadWritingSettings().catch(error=>{$('writing-status').textContent=error.message;});
document.querySelector('.tabs [data-view=settings]')?.addEventListener('click',loadWriting);
$('office-settings')?.addEventListener('click',loadWriting);

function setupHumanizerSheet(restricted){
  humanizerSheet=!!restricted;
  if(!restricted)return;
  $('humanizer-chat').disabled=false;
  $('humanizer-body').append($('writing-panel'));
  $('writing-heading').textContent='Humanizer';
  $('writing-intro').textContent='Paste a few paragraphs you wrote yourself, in English and in Georgian. Humanizer writes like them. To use it, attach a document in the chat and ask, for example, "humanize this, typos 0.5%".';
  $('writing-label-en').textContent='Style copy — English';$('writing-label-ka').textContent='Style copy — Georgian';
  $('writing-typo-rate').closest('label').firstChild.data='Typos (% of words)';
  $('writing-punct-rate').closest('label').firstChild.data='Punctuation removed (% of marks)';
  $('humanizer-chat').onclick=()=>{closeChatMenu();$('writing-status').textContent='';loadWriting();$('humanizer-dialog').showModal();};
  const close=()=>$('humanizer-dialog').close();
  $('humanizer-close').onclick=close;$('humanizer-x').onclick=close;
  const actions=document.createElement('div');actions.className='humanizer-actions';  // Save and Close side by side
  const save=$('writing-form').querySelector('button.primary');save.after(actions);actions.append(save,$('humanizer-close'));
}
