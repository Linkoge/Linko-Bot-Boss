/* One parser for streamed, reconciled and archived replies. No HTML insertion. */
'use strict';
const chatMarkdownParser=window.markdownit({html:false,linkify:true,breaks:false,typographer:false});
const chatMarkdownTags=new Set(['p','h1','h2','h3','h4','h5','h6','blockquote','ul','ol','li','strong','em','s','a','table','thead','tbody','tr','th','td']);
function safeChatLink(value){
  // The URL parser also rejects disguised schemes using whitespace/control chars.
  try{const url=new URL(value,location.href);return ['http:','https:','mailto:'].includes(url.protocol)?value:null;}catch{return null;}
}
function codeCopyButton(text){
  const button=document.createElement('button');button.type='button';button.className='code-copy';button.textContent='Copy';
  button.setAttribute('aria-label','Copy code block');
  // copyText (app.js) uses the Clipboard API and falls back to a hidden textarea on non-secure pages.
  button.onclick=async()=>{
    let label='Copied';
    try{await copyText(text);}catch{label='Copy failed';}
    button.textContent=label;clearTimeout(button._reset);button._reset=setTimeout(()=>{button.textContent='Copy';},1500);
  };
  return button;
}
function renderChatMarkdown(source){
  const fragment=document.createDocumentFragment();
  function append(tokens,parent){
    const stack=[parent];
    for(const token of tokens){
      const current=stack.at(-1);
      if(token.type==='inline'){append(token.children||[],current);continue;}
      if(token.nesting===-1){if(stack.length>1)stack.pop();continue;}
      if(token.nesting===1){
        // Hidden tight-list paragraphs remain wrappers so nesting stays balanced.
        const node=document.createElement(chatMarkdownTags.has(token.tag)?token.tag:'span');
        if(token.hidden)node.style.display='contents';
        if(token.tag==='a'){
          const href=safeChatLink(token.attrGet('href'));if(href!==null)node.setAttribute('href',href);
          const title=token.attrGet('title');if(title)node.setAttribute('title',title);
          node.setAttribute('rel','noopener noreferrer');
        }
        if(token.tag==='table'){node.tabIndex=0;node.setAttribute('aria-label','Markdown table');}
        if(token.tag==='ol'&&/^\d+$/.test(token.attrGet('start')||''))node.setAttribute('start',token.attrGet('start'));
        if(['th','td'].includes(token.tag)){
          const align=(token.attrGet('style')||'').match(/^text-align:(left|center|right)$/);if(align)node.style.textAlign=align[1];
        }
        current.append(node);stack.push(node);continue;
      }
      if(['fence','code_block'].includes(token.type)){
        const pre=document.createElement('pre'),code=document.createElement('code');
        code.textContent=token.content;pre.tabIndex=0;pre.setAttribute('aria-label','Code block');pre.append(code);
        const block=document.createElement('div');block.className='code-block';block.append(pre,codeCopyButton(token.content));current.append(block);
      }else if(token.type==='code_inline'){
        const code=document.createElement('code');code.textContent=token.content;current.append(code);
      }else if(token.type==='hardbreak')current.append(document.createElement('br'));
      else if(token.type==='softbreak')current.append(document.createTextNode('\n'));
      else if(token.type==='hr')current.append(document.createElement('hr'));
      // Images are readable alt text, never unsolicited external requests.
      else current.append(document.createTextNode(token.type==='image'?(token.content||'Image'):token.content));
    }
  }
  append(chatMarkdownParser.parse(source,{}),fragment);return fragment;
}
