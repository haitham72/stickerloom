/* Feature scopes own their timers and invalidate async work on navigation. */
const ROUTELIFE = new Set();
function routeOwner(matches, onEnter=()=>{}, onLeave=()=>{}) {
  let version=0, timer=0, active=false;
  const owner={
    capture:()=>version,
    current:token=>token===version,
    leave(){version++;active=false;clearTimeout(timer);timer=0;onLeave();},
    enter(name){active=matches(name);if(active)onEnter();},
    schedule(work,delay){
      clearTimeout(timer);timer=0;
      if(!active)return;
      const token=version;
      timer=setTimeout(()=>{timer=0;if(active&&owner.current(token))return work();},delay);
    }
  };
  ROUTELIFE.add(owner);
  return owner;
}
