'use strict';
// Instance UI language (English / Georgian). Main never calls setupInstanceUi(true) and stays English.
// All Georgian text lives in KA below: exact English string -> Georgian. The walker translates text nodes and the
// title/aria-label/placeholder attributes of the whole page (not chat messages) and keeps the English original so
// switching back restores it. Per profile: the choice is kept in this browser's localStorage.
const LANGUAGE_KEY='linko-language';
const KA={
  'Chats':'ჩატები','Search chats':'ჩატების ძიება','Search chats…':'ჩატების ძიება…','Settings':'პარამეტრები',
  'Start a new chat':'ახალი ჩატის დაწყება','+ New':'+ ახალი','Conversation':'საუბარი','Search in chat':'ძიება ჩატში',
  'Chat controls':'ჩატის მართვა','Chat history actions':'ჩატის ისტორიის მოქმედებები','Clear this chat':'ჩატის გასუფთავება',
  'Clear':'გასუფთავება','Archive all':'მთლიანის დაარქივება','Copy 5':'ბოლო 5-ის კოპირება','Copy all':'ყველაფრის კოპირება',
  'Copy the latest five messages':'ბოლო ხუთი შეტყობინების კოპირება','Copy the whole chat, including earlier messages':'მთელი ჩატის კოპირება ადრინდელი შეტყობინებებითურთ',
  'Restore Trash':'კალათის აღდგენა','Empty Trash':'კალათის დაცლა','Cleared chat trash':'გასუფთავებული ჩატის კალათა',
  'Humanizer':'ჰიუმანაიზერი','Message Bot Boss':'მისწერეთ Bot Boss-ს','Send message':'შეტყობინების გაგზავნა','Stop reply':'პასუხის შეჩერება',
  'Attach file or photo':'ფაილის ან ფოტოს მიმაგრება','Select files or photos':'აირჩიეთ ფაილები ან ფოტოები','Record microphone':'მიკროფონის ჩაწერა',
  'Draft attachments':'მიმაგრებული ფაილები','Message controls':'შეტყობინების მართვა','Retry':'თავიდან ცდა','Download':'ჩამოტვირთვა',
  'Upload':'ატვირთვა','Discard':'გაუქმება','Discard recording':'ჩანაწერის გაუქმება','Upload audio file instead of recording':'ჩანაწერის ნაცვლად აუდიოფაილის ატვირთვა',
  'Dismiss notification':'შეტყობინების დახურვა','Dismiss compaction notice':'შეტყობინების დახურვა',
  'Search current and archived chats':'მიმდინარე და დაარქივებული ჩატების ძიება','Search current and archived chats…':'მიმდინარე და დაარქივებული ჩატების ძიება…',
  'More chats':'მეტი ჩატი','Read full chat':'მთელი ჩატის წაკითხვა','Load earlier messages':'ადრინდელი შეტყობინებების ჩატვირთვა',
  'Version':'ვერსია','Bot Boss':'Bot Boss','Auto-update from GitHub':'ავტომატური განახლება GitHub-იდან','Update now':'განახლება ახლავე',
  'Language':'ენა','Instructions':'ინსტრუქციები','Apply across projects':'მოქმედებს ყველა პროექტზე','Save':'შენახვა',
  'Session':'სესია','Sign out':'გასვლა','History':'ისტორია','Close':'დახურვა','Edit':'რედაქტირება','Cancel':'გაუქმება',
  'Archive name':'არქივის სახელი','Confirm':'დადასტურება','Delete chat?':'წაიშალოს ჩატი?','Delete':'წაშლა','Rename':'გადარქმევა',
  'Pin':'მიმაგრება','Unpin':'მიმაგრების მოხსნა','Rename chat':'ჩატის გადარქმევა','Chat name':'ჩატის სახელი',
  'Archive all?':'დაარქივდეს მთლიანად?','Clear this chat?':'გასუფთავდეს ეს ჩატი?','Clear chat':'ჩატის გასუფთავება',
  'No chats match.':'ჩატები ვერ მოიძებნა.','No chats yet.':'ჩატები ჯერ არ არის.',
  'Wait until your message is sent, then switch chats.':'დაელოდეთ შეტყობინების გაგზავნას, შემდეგ გადართეთ ჩატი.',
  'Wait until the file finishes uploading, then switch chats.':'დაელოდეთ ფაილის ატვირთვის დასრულებას, შემდეგ გადართეთ ჩატი.',
  'Writing':'წერის სტილი','Style copy — English':'სტილის ნიმუში — ინგლისური','Style copy — Georgian':'სტილის ნიმუში — ქართული',
  'Paste a few paragraphs you wrote yourself, in English and in Georgian. Humanizer writes like them. To use it, attach a document in the chat and ask, for example, "humanize this, typos 0.5%".':
    'ჩასვით რამდენიმე აბზაცი, რომელიც თავად დაწერეთ, ინგლისურად და ქართულად. ჰიუმანაიზერი მათ მსგავსად დაწერს. გამოსაყენებლად მიამაგრეთ დოკუმენტი ჩატში და მოითხოვეთ, მაგალითად: „გააადამიანურე ეს, ბეჭდვითი შეცდომები 0.5%“.',
  'Typos (% of words)':'ბეჭდვითი შეცდომები (სიტყვების %)','Punctuation removed (% of marks)':'წაშლილი პუნქტუაცია (ნიშნების %)',
  'No sample saved.':'ნიმუში არ არის შენახული.','Humanizer settings saved.':'ჰიუმანაიზერის პარამეტრები შენახულია.',
  'Measured':'გაზომილია','sentences':'წინადადება','words per sentence':'სიტყვა წინადადებაში','commas per sentence':'მძიმე წინადადებაში',
  'sentences per paragraph':'წინადადება აბზაცში','per 1000 words':'1000 სიტყვაზე',
};
function currentLanguage(){try{return localStorage.getItem(LANGUAGE_KEY)==='ka'?'ka':'en';}catch{return 'en';}}
let i18nActive=false;
function tr(text){return i18nActive&&currentLanguage()==='ka'?(KA[text]??text):text;}
function trWhole(base){const t=base.trim();if(!t||!(t in KA))return base;return base.replace(t,KA[t]);}
const textRecords=new WeakMap();
const I18N_ATTRS=['title','aria-label','placeholder'];
function i18nSkip(node){return !!node.parentElement?.closest('#messages,#reader-content,textarea,script,style,.message');}
function i18nText(node){
  if(i18nSkip(node))return;
  const rec=textRecords.get(node),base=rec&&node.data===rec.shown?rec.base:node.data;
  const shown=currentLanguage()==='ka'?trWhole(base):base;
  if(shown!==node.data)node.data=shown;
  textRecords.set(node,{base,shown});
}
function i18nAttrs(element){
  if(element.closest?.('#messages,#reader-content'))return;
  const records=element.i18nAttrs||(element.i18nAttrs={});
  for(const name of I18N_ATTRS){
    if(!element.hasAttribute(name))continue;
    const value=element.getAttribute(name),rec=records[name],base=rec&&value===rec.shown?rec.base:value;
    const shown=currentLanguage()==='ka'?trWhole(base):base;
    if(shown!==value)element.setAttribute(name,shown);
    records[name]={base,shown};
  }
}
function i18nWalk(root){
  if(root.nodeType===3){i18nText(root);return;}
  if(root.nodeType!==1)return;
  i18nAttrs(root);
  const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT|NodeFilter.SHOW_ELEMENT);
  for(let node=walker.nextNode();node;node=walker.nextNode())node.nodeType===3?i18nText(node):i18nAttrs(node);
}
let i18nObserver=null;
function applyLanguage(){
  document.documentElement.lang=i18nActive&&currentLanguage()==='ka'?'ka':'en';
  i18nWalk(document.body);
  if(typeof writingSettings!=='undefined'&&writingSettings&&typeof renderWritingSettings==='function')renderWritingSettings(writingSettings);
}
function setupInstanceUi(restricted){
  i18nActive=!!restricted;
  if(!restricted)return;
  const select=document.getElementById('language-select');
  select.value=currentLanguage();
  select.onchange=()=>{try{localStorage.setItem(LANGUAGE_KEY,select.value);}catch{}applyLanguage();};
  applyLanguage();
  i18nObserver?.disconnect();
  i18nObserver=new MutationObserver(records=>{
    for(const record of records){
      if(record.type==='childList')record.addedNodes.forEach(i18nWalk);
      else if(record.type==='characterData')i18nText(record.target);
      else i18nAttrs(record.target);
    }
  });
  i18nObserver.observe(document.body,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:I18N_ATTRS});
}
