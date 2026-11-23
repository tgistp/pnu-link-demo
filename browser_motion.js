// Keep the previous rendered view until the next Streamlit view is complete.
// Routing stays in Python; this module only observes public navigation events.
export default function(component) {
    const {setTriggerValue,parentElement}=component;
    const host=parentElement.host||parentElement;
    const root=document.documentElement;
    const reduce=window.matchMedia('(prefers-reduced-motion: reduce)');
    const stateKey=Symbol.for('pnu.view-motion');
    const state=document[stateKey]||(document[stateKey]={active:null,count:0});
    state.scrolls||=new Map();
    host.dataset.pnuHistoryReady='true';

    const configure=()=>{
        root.dataset.pnuMotion=(!reduce.matches&&document.startViewTransition)?'native':'fallback';
        if(reduce.matches&&state.active)state.active.transition?.skipTransition();
    };
    configure();
    reduce.addEventListener('change',configure);
    const marker=()=>Array.from(document.querySelectorAll('.pnu-view-ready')).reduce(
        (latest,element)=>!latest||Number(element.dataset.viewGen)>Number(latest.dataset.viewGen)?element:latest,null);
    const main=()=>document.querySelector('[data-testid="stMain"]')||document.scrollingElement;
    const rememberScroll=()=>{
        const current=marker();
        if(current)state.scrolls.set(current.dataset.viewRoute,main().scrollTop);
    };
    const settleScroll=()=>{
        if(root.dataset.pnuFavoritePending==='true'&&(!document.querySelector('iframe[src*="pnu_catalog"]')||
            (state.favoriteAck&&document.querySelector('[data-testid="stApp"]')?.getAttribute('data-test-script-state')==='notRunning'))){
            root.dataset.pnuFavoritePending='false';clearTimeout(state.favoriteTimer);
        }
        const current=marker();
        if(!current||current.dataset.viewGen===state.scrollGeneration||state.scrollPending===current.dataset.viewGen)return;
        if(document.querySelector('[data-testid="stApp"]')?.getAttribute('data-test-script-state')!=='notRunning')return;
        const frames=Array.from(document.querySelectorAll('.st-key-view_transition iframe')).filter(frame=>frame.src.includes('pnu_catalog'));
        if(frames.some(frame=>frame.dataset.pnuContentReady!=='true'))return;
        const generation=current.dataset.viewGen,route=current.dataset.viewRoute;
        const position=state.restoreScroll?(state.scrolls.get(route)||0):0;
        state.scrollPending=generation;
        requestAnimationFrame(()=>requestAnimationFrame(()=>{
            state.scrollPending=null;
            if(marker()?.dataset.viewGen!==generation){settleScroll();return;}
            main().scrollTo({top:position,behavior:'instant'});
            state.scrollGeneration=generation;state.restoreScroll=false;
        }));
    };
    const scrollObserver=new MutationObserver(settleScroll);
    scrollObserver.observe(document.body,{subtree:true,childList:true,attributes:true,
        attributeFilter:['data-view-gen','data-test-script-state','data-pnu-content-ready']});
    settleScroll();

    function begin(restore=false) {
        root.dataset.pnuFavoritePending='false';clearTimeout(state.favoriteTimer);
        rememberScroll();state.restoreScroll=restore;
        if(reduce.matches||!document.startViewTransition||state.active)return;
        const previous=marker()?.dataset.viewGen;
        if(previous===undefined)return;
        let resolveReady;
        const ready=new Promise(resolve=>{resolveReady=resolve;});
        const active={transition:null,observer:null,timer:null,settling:false};
        state.active=active;
        // The content moves back in the opposite direction; navigation stays visually fixed.
        root.style.setProperty('--pnu-enter-y',restore?'-10px':'12px');
        root.style.setProperty('--pnu-exit-y',restore?'6px':'-6px');
        root.dataset.pnuTransitionState='waiting';
        root.dataset.pnuTransitionCount=String(++state.count);
        const release=()=>{
            active.observer?.disconnect();
            clearTimeout(active.timer);
            resolveReady();
        };
        const check=()=>{
            const next=marker()?.dataset.viewGen;
            const running=document.querySelector('[data-testid="stApp"]')?.getAttribute('data-test-script-state');
            if(active.settling||next===undefined||next===previous||running!=='notRunning')return;
            const frames=Array.from(document.querySelectorAll('.st-key-view_transition iframe')).filter(frame=>frame.src.includes('pnu_catalog'));
            if(frames.some(frame=>frame.dataset.pnuContentReady!=='true'))return;
            active.settling=true;
            // Wait for the final layout and promptly loaded preview images, not a fixed delay.
            requestAnimationFrame(()=>requestAnimationFrame(async()=>{
                const images=Array.from(document.querySelectorAll('.st-key-view_transition img')).slice(0,9);
                await Promise.race([
                    Promise.all(images.map(image=>image.complete?Promise.resolve():new Promise(resolve=>{
                        image.addEventListener('load',resolve,{once:true});
                        image.addEventListener('error',resolve,{once:true});
                    }))),
                    new Promise(resolve=>setTimeout(resolve,220))
                ]);
                release();
            }));
        };
        active.observer=new MutationObserver(check);
        active.observer.observe(document.body,{subtree:true,childList:true,attributes:true,
            attributeFilter:['data-view-gen','data-test-script-state','data-pnu-content-ready']});
        // Slow requests or validation must never leave an unresponsive screenshot overlay.
        active.timer=setTimeout(release,1800);
        try {
            active.transition=document.startViewTransition(()=>ready);
            active.transition.ready.then(()=>{root.dataset.pnuTransitionState='animating';}).catch(()=>{});
            active.transition.finished.catch(()=>{}).finally(()=>{
                release();
                if(state.active===active){state.active=null;root.dataset.pnuTransitionState='idle';}
            });
        } catch {
            release();state.active=null;root.dataset.pnuTransitionState='idle';
        }
    }

    const routeControls='.st-key-main_menu,.st-key-registration_kind,.st-key-go_home,.st-key-detail_back,'+
        '[class*="st-key-detail_search_"],[class*="st-key-detail_recommendations_"],[class*="st-key-detail_favorites_"]';
    const navigationLabels=new Set(['목록으로 돌아가기','내 등록물 보기','추천 물건 보기',
        '물건 찾기','분실 신고 남기기','내역에서 확인','다른 물건 등록']);
    const onClick=event=>{
        if(!(event.target instanceof Element))return;
        const control=event.target.closest(routeControls);
        const button=event.target.closest('button');
        if(!control&&(!button||!navigationLabels.has(button.innerText.trim())))return;
        const option=event.target.closest('[role="radio"],label');
        if(option&&(option.getAttribute('aria-checked')==='true'||option.querySelector('input:checked')))return;
        if(button?.dataset.selected==='true')return;
        begin(Boolean(event.target.closest('.st-key-detail_back'))||button?.innerText.trim()==='목록으로 돌아가기');
    };
    const onPop=()=>{
        begin(true);
        setTriggerValue('navigate',{route:(new URL(window.location.href).searchParams.get('route')||'').slice(0,81)});
    };
    const onLanding=event=>{
        if(['pnu:favoritePending','pnu:favoriteSettled'].includes(event.data?.type)){
            const frame=Array.from(document.querySelectorAll('iframe')).find(frame=>frame.contentWindow===event.source&&frame.src.includes('pnu_catalog'));
            if(!frame)return;
            if(event.data.type==='pnu:favoritePending'){
                clearTimeout(state.favoriteTimer);state.favoriteAck=false;
                root.dataset.pnuFavoritePending='true';
                state.favoriteTimer=setTimeout(()=>{root.dataset.pnuFavoritePending='false';},12500);
            }else{state.favoriteAck=true;settleScroll();}
            return;
        }
        if(event.data?.type==='pnu:contentReady'){
            const frame=Array.from(document.querySelectorAll('iframe')).find(frame=>frame.contentWindow===event.source&&frame.src.includes('pnu_catalog'));
            if(frame)frame.dataset.pnuContentReady='true';
            return;
        }
        if(event.data?.type==='streamlit:setComponentValue'&&event.data.value?.action==='detail'){
            const frame=Array.from(document.querySelectorAll('iframe')).find(frame=>frame.contentWindow===event.source&&frame.src.includes('pnu_catalog'));
            if(frame)begin();
            return;
        }
        if(event.data?.type!=='streamlit:setComponentValue'||!['lost','found'].includes(event.data?.value))return;
        const frames=Array.from(document.querySelectorAll('iframe'));
        if(frames.some(frame=>frame.contentWindow===event.source&&frame.src.includes('campus_start')))begin();
    };
    document.addEventListener('click',onClick,true);
    window.addEventListener('popstate',onPop,true);
    window.addEventListener('message',onLanding);
    return ()=>{
        document.removeEventListener('click',onClick,true);
        window.removeEventListener('popstate',onPop,true);
        window.removeEventListener('message',onLanding);
        reduce.removeEventListener('change',configure);
        scrollObserver.disconnect();
        delete host.dataset.pnuHistoryReady;
        // An in-flight transition survives component rerenders and releases itself.
    };
}
