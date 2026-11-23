// Routing stays in Python. Motion never blocks clicks or waits for iframe animation.
export default function({setTriggerValue,parentElement}) {
    const host=parentElement.host||parentElement,root=document.documentElement;
    const reduce=window.matchMedia('(prefers-reduced-motion: reduce)');
    const key=Symbol.for('pnu.view-motion');
    const state=document[key]||(document[key]={count:0});state.scrolls||=new Map();
    state.active?.transition?.skipTransition();state.active=null;
    host.dataset.pnuHistoryReady='true';root.dataset.pnuMotion='nonblocking';
    const marker=()=>Array.from(document.querySelectorAll('.pnu-view-ready')).reduce(
        (a,b)=>!a||Number(b.dataset.viewGen)>Number(a.dataset.viewGen)?b:a,null);
    const main=()=>document.querySelector('[data-testid="stMain"]')||document.scrollingElement;
    const running=()=>document.querySelector('[data-testid="stApp"]')?.getAttribute('data-test-script-state')!=='notRunning';
    function settle(){
        if(root.dataset.pnuFavoritePending==='true'&&(!document.querySelector('iframe[src*="pnu_catalog"]')||
            (state.favoriteAck&&!running()))){
            root.dataset.pnuFavoritePending='false';clearTimeout(state.favoriteTimer);
        }
        const current=marker();
        if(!current||running()||current.dataset.viewGen===state.scrollGeneration)return;
        const returning=Boolean(state.restoreScroll);
        main().scrollTo({top:returning?(state.scrolls.get(current.dataset.viewRoute)||0):0,behavior:'instant'});
        state.scrollGeneration=current.dataset.viewGen;state.restoreScroll=false;
        root.dataset.pnuTransitionState='idle';clearTimeout(state.navigationTimer);
        // Reveal only the arriving body. No snapshot overlay or animation-completion promise.
        if(state.pending&&!reduce.matches){
            state.entrance?.cancel();
            state.entrance=document.querySelector('.st-key-view_transition')?.animate([
                {opacity:.86,transform:`translateY(${returning?-5:6}px)`},
                {opacity:1,transform:'translateY(0)'}
            ],{duration:190,easing:'cubic-bezier(.22,1,.36,1)'});
        }
        state.pending=false;
        root.dataset.pnuNavigationMs=String(Math.round(performance.now()-(state.started||performance.now())));
    }
    let scheduled=0;
    const schedule=()=>{if(!scheduled)scheduled=requestAnimationFrame(()=>{scheduled=0;settle()})};
    const observer=new MutationObserver(schedule);
    observer.observe(document.body,{subtree:true,childList:true,attributes:true,
        attributeFilter:['data-view-gen','data-test-script-state']});schedule();
    function begin(restore=false){
        const current=marker();if(current)state.scrolls.set(current.dataset.viewRoute,main().scrollTop);
        state.restoreScroll=restore;state.pending=true;state.started=performance.now();state.entrance?.cancel();
        root.dataset.pnuFavoritePending='false';clearTimeout(state.favoriteTimer);
        root.dataset.pnuTransitionState='updating';root.dataset.pnuTransitionCount=String(++state.count);
        // Failure cleanup only; navigation and input never wait for this timer.
        clearTimeout(state.navigationTimer);
        state.navigationTimer=setTimeout(()=>{root.dataset.pnuTransitionState='idle';},5000);
    }
    const controls='.st-key-main_menu,.st-key-registration_kind,.st-key-go_home,.st-key-detail_back,'+
        '[class*="st-key-detail_search_"],[class*="st-key-detail_recommendations_"],[class*="st-key-detail_favorites_"]';
    const labels=new Set(['목록으로 돌아가기','내 등록물 보기','추천 물건 보기',
        '물건 찾기','분실 신고 남기기','내역에서 확인','다른 물건 등록']);
    const onClick=event=>{
        if(!(event.target instanceof Element))return;
        const control=event.target.closest(controls),button=event.target.closest('button');
        if(!control&&(!button||!labels.has(button.innerText.trim())))return;
        const option=event.target.closest('[role="radio"],label');
        if(option&&(option.getAttribute('aria-checked')==='true'||option.querySelector('input:checked')))return;
        if(button?.dataset.selected==='true')return;
        begin(Boolean(event.target.closest('.st-key-detail_back'))||button?.innerText.trim()==='목록으로 돌아가기');
    };
    const onPop=()=>{begin(true);setTriggerValue('navigate',{
        route:(new URL(window.location.href).searchParams.get('route')||'').slice(0,81)})};
    const onMessage=event=>{
        const frame=Array.from(document.querySelectorAll('iframe')).find(f=>f.contentWindow===event.source);
        if(!frame)return;
        if(frame.src.includes('pnu_catalog')){
            if(event.data?.type==='pnu:favoritePending'){
                clearTimeout(state.favoriteTimer);state.favoriteAck=false;root.dataset.pnuFavoritePending='true';
                state.favoriteTimer=setTimeout(()=>{root.dataset.pnuFavoritePending='false';},12500);
            }else if(event.data?.type==='pnu:favoriteSettled'){state.favoriteAck=true;schedule();}
            else if(event.data?.type==='streamlit:setComponentValue'&&event.data.value?.action==='detail')begin();
        }else if(frame.src.includes('campus_start')&&event.data?.type==='streamlit:setComponentValue'&&
                 ['lost','found'].includes(event.data.value))begin();
    };
    document.addEventListener('click',onClick,true);window.addEventListener('popstate',onPop,true);
    window.addEventListener('message',onMessage);
    return()=>{
        observer.disconnect();cancelAnimationFrame(scheduled);
        document.removeEventListener('click',onClick,true);window.removeEventListener('popstate',onPop,true);
        window.removeEventListener('message',onMessage);delete host.dataset.pnuHistoryReady;
    };
}
