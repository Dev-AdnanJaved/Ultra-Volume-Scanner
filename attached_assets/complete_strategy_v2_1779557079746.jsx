import { useState } from "react";

const C = {
  bg:"#07070f",s1:"#0d0d1a",s2:"#111120",s3:"#161628",
  border:"#1a1a2e",border2:"#252540",
  green:"#00e5a0",blue:"#4facfe",purple:"#a29bfe",
  orange:"#ff9f43",red:"#ff5e7e",yellow:"#ffd32a",
  cyan:"#00d2ff",text:"#dde0f0",muted:"#5a5a80",
};
const mono={fontFamily:"monospace"};

const Tag=({c=C.green,children})=>(
  <span style={{...mono,fontSize:10,letterSpacing:2,textTransform:"uppercase",padding:"3px 9px",
    borderRadius:2,border:`1px solid ${c}44`,background:c+"10",color:c,display:"inline-block"}}>{children}</span>
);
const Box=({color=C.green,children,style={}})=>(
  <div style={{background:color+"09",border:`1px solid ${color}30`,borderRadius:5,
    padding:"14px 18px",marginBottom:12,...style}}>{children}</div>
);
const SectionTitle=({n,title,sub})=>(
  <div style={{borderBottom:`1px solid ${C.border}`,paddingBottom:12,marginBottom:16,marginTop:28}}>
    <div style={{display:"flex",gap:10,alignItems:"center"}}>
      <span style={{...mono,fontSize:11,color:C.green,background:C.green+"15",
        border:`1px solid ${C.green}30`,borderRadius:3,padding:"2px 8px"}}>{n}</span>
      <span style={{fontSize:16,fontWeight:800,letterSpacing:-0.3}}>{title}</span>
    </div>
    {sub&&<div style={{fontSize:12,color:C.muted,marginTop:4,marginLeft:42}}>{sub}</div>}
  </div>
);

// THE SCORE COMPONENT — used at every TP
const ScoreTable=({tpLevel,data,nextTp,timeLimit,note,invertSpeed})=>(
  <div style={{background:C.s1,border:`1px solid ${C.border}`,borderRadius:5,overflow:"hidden",marginBottom:12}}>
    <div style={{background:C.s2,padding:"9px 14px",display:"flex",justifyContent:"space-between",alignItems:"center"}}>
      <span style={{...mono,fontSize:10,color:C.muted,letterSpacing:2}}>SCORE SYSTEM AT TP{tpLevel}</span>
      <span style={{...mono,fontSize:11,color:C.orange}}>⏱ EXIT if TP{nextTp} not hit within {timeLimit} of this TP</span>
    </div>
    {/* Scoring rules */}
    <div style={{padding:"10px 14px",borderBottom:`1px solid ${C.border}`}}>
      <div style={{...mono,fontSize:10,color:C.muted,letterSpacing:1,marginBottom:8}}>HOW TO SCORE (0–10)</div>
      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:6,fontSize:12}}>
        {[
          invertSpeed?
            [["Hit in <12h","+2",C.blue],["Hit 12-48h","+1",C.blue],["Hit 48h+","+0",C.muted]] :
            [["Hit in <3h","+3",C.green],["Hit 3-6h","+2",C.green],["Hit 6-12h","+1",C.blue],["Hit 12h+","+0",C.muted]],
          [["4h mom ≥8%","+2",C.green],["4h mom 3-8%","+1",C.blue],["4h mom <3%","+0",C.muted]],
          [["1h mom ≥5%","+2",C.green],["1h mom 2-5%","+1",C.blue],["1h mom <2%","+0",C.muted]],
          [["OI ≥15%","+2",C.green],["OI 5-15%","+1",C.blue],["OI <5%","+0",C.muted]],
          [["MCap <$50M","+1",C.purple]],
        ].flat().map(([label,pts,col],i)=>(
          <div key={i} style={{display:"flex",justifyContent:"space-between",
            background:C.s3,borderRadius:3,padding:"4px 8px"}}>
            <span style={{color:C.muted}}>{label}</span>
            <span style={{...mono,fontWeight:700,color:col}}>{pts}</span>
          </div>
        ))}
      </div>
      {invertSpeed&&<div style={{fontSize:11,color:C.yellow,marginTop:8}}>
        ⚠ At TP{tpLevel}+, SLOW moves (+48h) can still continue. Don't penalise slow signals as harshly.
      </div>}
    </div>
    {/* Action table */}
    <div style={{padding:"10px 14px"}}>
      <div style={{...mono,fontSize:10,color:C.muted,letterSpacing:1,marginBottom:8}}>SCORE → ACTION</div>
      {data.map((row,i)=>(
        <div key={i} style={{display:"flex",gap:8,padding:"8px 10px",marginBottom:4,
          background:row.color+"0d",border:`1px solid ${row.color}30`,borderRadius:4,alignItems:"flex-start"}}>
          <div style={{...mono,fontWeight:800,color:row.color,fontSize:13,width:44,flexShrink:0,paddingTop:1}}>{row.score}</div>
          <div style={{flex:1}}>
            <div style={{fontWeight:700,fontSize:13,color:row.color,marginBottom:2}}>{row.action}</div>
            <div style={{fontSize:11,color:C.muted,lineHeight:1.6}}>{row.detail}</div>
          </div>
          <div style={{display:"flex",gap:6,flexShrink:0}}>
            {row.stats.map(([l,v],j)=>(
              <div key={j} style={{background:C.s2,borderRadius:3,padding:"4px 8px",textAlign:"center"}}>
                <div style={{...mono,fontSize:9,color:C.muted}}>{l}</div>
                <div style={{...mono,fontSize:12,fontWeight:700,color:row.color}}>{v}</div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
    {note&&<div style={{padding:"8px 14px",borderTop:`1px solid ${C.border}`,
      fontSize:11,color:C.yellow,background:C.yellow+"06"}}>{note}</div>}
  </div>
);

// SL timeline component
const SLTimeline=()=>(
  <div style={{background:C.s1,border:`1px solid ${C.border}`,borderRadius:5,padding:"16px",marginBottom:12}}>
    <div style={{...mono,fontSize:10,color:C.muted,letterSpacing:2,marginBottom:12}}>STOP LOSS RATCHET — ONLY EVER MOVES UP</div>
    {[
      {event:"ENTRY",sl:"-15% from entry",color:C.red,note:"Set BEFORE anything else. Non-negotiable."},
      {event:"TP5 hit",sl:"Move to 0% (entry)",color:C.orange,note:"You are now risk-free. Worst case = break even."},
      {event:"TP10 hit",sl:"Move to +5%",color:C.yellow,note:"Locked profit. Only exception: RIDE score (6+) → keep at 0% for room."},
      {event:"TP20 hit",sl:"Move to +12%",color:C.blue,note:"Meaningful locked profit. Position partially closed."},
      {event:"TP30 hit",sl:"Trail 10% below high",color:C.purple,note:"Switch to trailing. SL follows price up automatically."},
      {event:"TP50 hit",sl:"Trail 8% below high",color:C.green,note:"Tighten trail. You're in rare territory — protect the big gain."},
      {event:"TP75 hit",sl:"Trail 8% below high",color:C.green,note:"Keep trailing. 63% of TP75 signals hit TP100."},
    ].map((r,i,arr)=>(
      <div key={i} style={{display:"flex",gap:0}}>
        <div style={{display:"flex",flexDirection:"column",alignItems:"center",width:32,flexShrink:0}}>
          <div style={{width:10,height:10,borderRadius:"50%",background:r.color,
            boxShadow:`0 0 6px ${r.color}88`,flexShrink:0,marginTop:4}}/>
          {i<arr.length-1&&<div style={{width:1,flex:1,background:C.border,minHeight:16,margin:"3px 0"}}/>}
        </div>
        <div style={{paddingBottom:i<arr.length-1?14:0,paddingLeft:10,flex:1}}>
          <div style={{display:"flex",gap:8,alignItems:"center",marginBottom:2}}>
            <span style={{fontWeight:700,fontSize:13,color:r.color}}>{r.event}</span>
            <span style={{...mono,fontSize:11,background:r.color+"15",border:`1px solid ${r.color}40`,
              borderRadius:2,padding:"1px 7px",color:r.color}}>{r.sl}</span>
          </div>
          <div style={{fontSize:11,color:C.muted}}>{r.note}</div>
        </div>
      </div>
    ))}
  </div>
);

const TABS=["Entry","TP5","TP10","TP20","TP30","TP50+","SL Rules","Time Limits","Cheatsheet"];

export default function App(){
  const [tab,setTab]=useState(0);
  return(
    <div style={{background:C.bg,minHeight:"100vh",color:C.text,
      fontFamily:"'Segoe UI',system-ui,sans-serif",paddingBottom:60}}>

      <div style={{borderBottom:`1px solid ${C.border}`,padding:"26px 22px 20px",maxWidth:820,margin:"0 auto"}}>
        <Tag c={C.green}>Complete Strategy v2 · Every TP Covered · Data-Verified</Tag>
        <h1 style={{fontSize:20,fontWeight:800,letterSpacing:-0.5,margin:"10px 0 4px"}}>
          Full Exit & Hold System — <span style={{color:C.green}}>Updated with Time Limits + TP20/TP30 Scoring</span>
        </h1>
        <p style={{color:C.muted,fontSize:13,margin:0}}>
          199 signals · Every snapshot field used · Score at TP5, TP10, TP20, TP30 · Time limits at every level · TP50+ trail only
        </p>
      </div>

      <div style={{maxWidth:820,margin:"0 auto",padding:"12px 22px 0",display:"flex",gap:4,flexWrap:"wrap"}}>
        {TABS.map((t,i)=>(
          <button key={i} onClick={()=>setTab(i)} style={{
            padding:"5px 12px",fontSize:11,borderRadius:4,cursor:"pointer",fontFamily:"inherit",
            border:`1px solid ${tab===i?C.green:C.border}`,
            background:tab===i?C.green+"18":"transparent",
            color:tab===i?C.green:C.muted,fontWeight:tab===i?700:400,
          }}>{t}</button>
        ))}
      </div>

      <div style={{maxWidth:820,margin:"0 auto",padding:"16px 22px"}}>

        {/* ── ENTRY ── */}
        {tab===0&&(<div>
          <SectionTitle n="01" title="Entry Conditions — ALL must pass" sub="Check before opening any trade" />
          <div style={{background:C.s1,border:`1px solid ${C.border}`,borderRadius:5,overflow:"hidden",marginBottom:12}}>
            {[
              {icon:"📡",label:"BTC trend",val:"= ranging",note:"Bot field: btc_trend_at_entry. If pumping or dumping → SKIP entirely.",color:C.green,req:true},
              {icon:"📊",label:"Quality Score",val:"≥ 3 (not 7-8)",note:"QS 7-8 underperforms (38-55% TP5). Sweet spot is QS 3-6.",color:C.green,req:true},
              {icon:"💰",label:"Market Cap",val:"< $200M",note:"TP10 rate: 74% for <$50M vs 39% for $200M+. Ideal: <$50M.",color:C.green,req:true},
              {icon:"💸",label:"Funding rate",val:"≥ 0% and ≤ 0.15%",note:"Use funding_in_ideal_range = True. Negative = shorts dominating.",color:C.green,req:true},
              {icon:"📈",label:"OI change",val:"5% to 50%",note:"New money entering without overextension. Ideal: 8-25%.",color:C.green,req:true},
              {icon:"🔊",label:"Vol 24h",val:"≥ $5M (Premium: ≥ $20M)",note:"≥$20M = 95.5% TP5, 0% fail rate. Priority tier.",color:C.blue,req:false},
              {icon:"⏱",label:"Entry timing",val:"Within 1% of signal price",note:"If price already moved 3%+, skip. You missed the entry.",color:C.orange,req:false},
            ].map((r,i)=>(
              <div key={i} style={{display:"flex",gap:12,padding:"11px 16px",
                borderTop:i>0?`1px solid ${C.border}`:"none",alignItems:"flex-start"}}>
                <span style={{fontSize:18,flexShrink:0,width:28}}>{r.icon}</span>
                <div style={{width:110,flexShrink:0}}>
                  <div style={{fontSize:12,fontWeight:600}}>{r.label}</div>
                  {r.req&&<span style={{...mono,fontSize:9,color:C.red,background:C.red+"15",
                    padding:"1px 5px",borderRadius:2}}>REQUIRED</span>}
                </div>
                <div style={{...mono,fontSize:12,fontWeight:700,color:r.color,width:150,flexShrink:0}}>{r.val}</div>
                <div style={{fontSize:12,color:C.muted,flex:1}}>{r.note}</div>
              </div>
            ))}
          </div>
          <Box color={C.red}>
            <div style={{fontWeight:700,color:C.red,marginBottom:6,fontSize:13}}>Immediately after entering:</div>
            <div style={{fontSize:13,color:C.muted,lineHeight:1.8}}>
              Set stop loss at <strong style={{color:C.red}}>-15% from entry price</strong> before you do anything else.
              Then close the app. Your bot will alert at each TP. Do not watch the chart.
            </div>
          </Box>
        </div>)}

        {/* ── TP5 ── */}
        {tab===1&&(<div>
          <SectionTitle n="02" title="At TP5 Hit (+5%)" sub="Bot sends TP5 alert with full snapshot. Calculate score, execute action." />
          <Box color={C.orange}>
            <div style={{fontSize:13,color:C.muted,lineHeight:1.7}}>
              <strong style={{color:C.orange}}>Special rule first:</strong> If TP5 took <strong style={{color:C.red}}>24 hours or more</strong> to hit
              → EXIT 100% immediately regardless of score. Data shows: TP5 after 24h+ = only 52% TP20 rate.
              Momentum is gone. Take the 5% and free the slot.
            </div>
          </Box>
          <ScoreTable
            tpLevel={5} nextTp={10} timeLimit="48h" invertSpeed={false}
            note="⚡ After TP5: start the 48h clock. If TP10 not hit within 48h → exit remaining position. 89% of continuations happen within 48h."
            data={[
              {score:"0–1",color:C.red,action:"EXIT 100%",
                detail:"Slow hit (12h+), weak momentum, low OI. 40% TP10 rate — not worth the risk. Take guaranteed +5%.",
                stats:[["TP10","40%"],["n","14"]]},
              {score:"2–3",color:C.orange,action:"Close 60%, keep 40%",
                detail:"Some signals firing but not all. Lock most profit. SL moves to entry (0%). Keep small runner.",
                stats:[["TP10","91%"],["n","36"]]},
              {score:"4–5",color:C.blue,action:"Close 30%, keep 70%",
                detail:"Good momentum. High probability of TP10 and TP20. Move SL to entry. Let 70% ride.",
                stats:[["TP10","82%"],["n","39"]]},
              {score:"6+",color:C.green,action:"Close 10% only — RIDE",
                detail:"All signals strong. Potential TP50-TP100 runner. Keep SL at -15% (don't move to entry yet — give room). Recalculate at TP10.",
                stats:[["TP10","85%"],["n","85"]]},
            ]}
          />
        </div>)}

        {/* ── TP10 ── */}
        {tab===2&&(<div>
          <SectionTitle n="03" title="At TP10 Hit (+10%)" sub="Recalculate score using tp10_snapshot. Your score at TP10 overrides the TP5 plan." />
          <Box color={C.blue}>
            <div style={{fontSize:13,color:C.muted,lineHeight:1.7}}>
              <strong style={{color:C.blue}}>Start the clock:</strong> After TP10, if TP20 is not hit within <strong style={{color:C.yellow}}>72 hours</strong>,
              exit remaining position. 89% of continuations happen within 72h of TP10. After that only 11% chance it comes.
            </div>
          </Box>
          <ScoreTable
            tpLevel={10} nextTp={20} timeLimit="72h" invertSpeed={false}
            note="After TP10: SL moves to +5% minimum (for all scores except RIDE 6+). You are now guaranteed profit."
            data={[
              {score:"0–1",color:C.red,action:"EXIT remaining",
                detail:"Slow, weak OI, fading momentum. TP20 rate only 33%. Close everything and lock the 10%.",
                stats:[["TP20","33%"],["n","9"]]},
              {score:"2–3",color:C.orange,action:"Close 50% remaining, keep 50%",
                detail:"Mixed signals. Take half, let half run. SL moves to +5%.",
                stats:[["TP20","50%"],["n","34"]]},
              {score:"4–5",color:C.blue,action:"Close 20% remaining, keep 80%",
                detail:"Strong continuation. TP20: 71%, TP30: 53%. Move SL to +5%. Let it run.",
                stats:[["TP20","71%"],["n","42"]]},
              {score:"6+",color:C.green,action:"Close 10% remaining — RIDE",
                detail:"All signals firing. TP20: 75%+, TP30: 65%+. Move SL to 0% (give room). These are your runners.",
                stats:[["TP20","75%"],["n","59"]]},
            ]}
          />
        </div>)}

        {/* ── TP20 ── NEW ── */}
        {tab===3&&(<div>
          <SectionTitle n="04" title="At TP20 Hit (+20%) — NEW" sub="Score check added. Previously strategy had no decision logic here." />
          <Box color={C.green}>
            <div style={{fontSize:13,color:C.muted,lineHeight:1.7}}>
              <strong style={{color:C.green}}>New addition:</strong> Data shows score 0-2 at TP20 gives 72% TP30
              but score 6-8 gives <strong style={{color:C.green}}>88% TP30 and 68% TP50</strong>. Worth scoring here.
              SL moves to +12% at this level regardless of score.
              Start 48h clock for TP30.
            </div>
          </Box>
          <ScoreTable
            tpLevel={20} nextTp={30} timeLimit="48h" invertSpeed={false}
            note="At TP20, 96% of continuations to TP30 happen within 48h. If TP30 not hit in 48h → exit remaining. SL already at +12% so you keep profits either way."
            data={[
              {score:"0–2",color:C.orange,action:"Close 50% remaining",
                detail:"Weaker momentum at TP20. TP30 rate 72% — decent but OI/momentum fading. Take half, trail half.",
                stats:[["TP30","72%"],["TP50","40%"]]},
              {score:"3–5",color:C.blue,action:"Close 25% remaining, keep 75%",
                detail:"Solid continuation signals. TP30: 72%, TP50: 48%. Move SL to +12%. Strong hold.",
                stats:[["TP30","72%"],["TP50","48%"]]},
              {score:"6–8",color:C.green,action:"Close 10% only — full ride",
                detail:"All conditions strong. TP30: 88%, TP50: 68%. This is a runner. Trail SL at 10% below high.",
                stats:[["TP30","88%"],["TP50","68%"]]},
            ]}
          />
        </div>)}

        {/* ── TP30 ── NEW ── */}
        {tab===4&&(<div>
          <SectionTitle n="05" title="At TP30 Hit (+30%) — NEW" sub="Score logic inverted here: slow grind outperforms fast spike. Different scoring." />
          <Box color={C.purple}>
            <div style={{fontSize:13,color:C.muted,lineHeight:1.7}}>
              <strong style={{color:C.purple}}>Key insight from data:</strong> At TP30+, fast signals (&lt;6h) only hit TP50 at
              <strong style={{color:C.red}}> 43%</strong> while slow signals (24h+) hit TP50 at
              <strong style={{color:C.green}}> 69%</strong>. The pattern reverses here.
              A slow grind to TP30 often means sustained buying — more fuel left.
              Speed scoring is de-emphasised at this level. OI and momentum matter more.
              Start 48h clock for TP50.
            </div>
          </Box>
          <ScoreTable
            tpLevel={30} nextTp={50} timeLimit="48h" invertSpeed={true}
            note="⚠ Speed reversal: fast TP30 hit (<6h) = 43% TP50. Slow TP30 hit (24h+) = 69% TP50. Don't over-penalise slow signals here."
            data={[
              {score:"0–2",color:C.red,action:"EXIT remaining",
                detail:"Very weak: no OI growth, fading momentum. TP50 only 36%. Lock the 30% — it's an excellent trade already.",
                stats:[["TP50","36%"],["TP75","27%"]]},
              {score:"3–5",color:C.blue,action:"Close 30% remaining, keep 70%",
                detail:"Moderate continuation. TP50: 64%, TP75: 39%. Trail SL 10% below high on remainder.",
                stats:[["TP50","64%"],["TP75","39%"]]},
              {score:"6–8",color:C.green,action:"Trail only — close nothing",
                detail:"Strong: OI still rising, momentum intact. TP50: 88%, TP75: 50%. Pure trail stop from here. Let it run.",
                stats:[["TP50","88%"],["TP75","50%"]]},
            ]}
          />
        </div>)}

        {/* ── TP50+ ── */}
        {tab===5&&(<div>
          <SectionTitle n="06" title="TP50+ — Trail Stop Only" sub="Sample sizes too small for reliable scoring. Let the trail do the work." />
          <Box color={C.yellow}>
            <div style={{fontSize:13,color:C.muted,lineHeight:1.7}}>
              <strong style={{color:C.yellow}}>Why no score at TP50+?</strong> Only 49 signals reached TP50 in 40 days.
              Per score bucket that's fewer than 15 signals — too noisy to make reliable decisions from.
              At TP50+ you switch to <strong style={{color:C.text}}>pure trail stop management</strong>.
              The score system has already done its job.
            </div>
          </Box>
          {[
            {tp:"TP50 (+50%)",next:"TP75",cont:"61%",timeLimit:"48h",color:C.orange,
              actions:[
                "Close 30% of remaining position at TP50",
                "Tighten trail SL from 10% → 8% below running high",
                "Start 48h clock: if TP75 not hit within 48h → exit remaining",
                "97% of continuations to TP75 happen within 48h",
              ]},
            {tp:"TP75 (+75%)",next:"TP100",cont:"63%",timeLimit:"24h",color:C.blue,
              actions:[
                "Close 30% of remaining position at TP75",
                "Keep trail SL at 8% below running high",
                "Start 24h clock: if TP100 not hit within 24h → exit remaining",
                "95% of continuations to TP100 happen within 24h — it moves fast from here",
              ]},
            {tp:"TP100 (+100%)",next:"Beyond",cont:"—",timeLimit:"—",color:C.green,
              actions:[
                "Close 50% of remaining at TP100 — you've doubled",
                "Keep 50% with very loose 12% trail",
                "2 consecutive red 4h candles → exit all remaining",
                "Some signals went 200%, 385%, 5770% from here — let runners run",
              ]},
          ].map((r,i)=>(
            <div key={i} style={{background:C.s1,border:`1px solid ${r.color}35`,
              borderLeft:`3px solid ${r.color}`,borderRadius:5,padding:"14px 18px",marginBottom:10}}>
              <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",flexWrap:"wrap",gap:8,marginBottom:10}}>
                <div>
                  <div style={{fontWeight:800,fontSize:15,color:r.color}}>{r.tp}</div>
                  <div style={{fontSize:12,color:C.muted,marginTop:2}}>{r.cont!=="—"?`${r.cont} continue to ${r.next}`:"You are here — rare territory"}</div>
                </div>
                {r.timeLimit!=="—"&&(
                  <div style={{...mono,fontSize:11,color:C.orange,background:C.orange+"12",
                    border:`1px solid ${C.orange}30`,borderRadius:3,padding:"4px 10px"}}>
                    ⏱ Exit if {r.next} not hit within {r.timeLimit}
                  </div>
                )}
              </div>
              {r.actions.map((a,j)=>(
                <div key={j} style={{display:"flex",gap:8,padding:"4px 0",fontSize:12}}>
                  <span style={{color:r.color,flexShrink:0}}>→</span>
                  <span style={{color:C.muted}}>{a}</span>
                </div>
              ))}
            </div>
          ))}
          <Box color={C.red}>
            <div style={{fontSize:13,color:C.muted,lineHeight:1.7}}>
              <strong style={{color:C.red}}>Emergency exit at any TP level:</strong> BTC trend switches to dumping mid-trade,
              OR funding rate spikes above 0.3%, OR OI drops sharply negative, OR price reverses 15%+ from peak rapidly
              → exit everything immediately regardless of SL level.
            </div>
          </Box>
        </div>)}

        {/* ── SL RULES ── */}
        {tab===6&&(<div>
          <SectionTitle n="07" title="Stop Loss Rules" sub="SL is a one-way ratchet — only ever moves UP, never down." />
          <SLTimeline />
          <div style={{background:C.s1,border:`1px solid ${C.border}`,borderRadius:5,padding:"16px",marginBottom:12}}>
            <div style={{...mono,fontSize:10,color:C.muted,letterSpacing:2,marginBottom:12}}>EXCEPTIONS</div>
            {[
              {t:"RIDE score (6+) at TP5",c:C.green,
                r:"Keep SL at -15% until TP10 (don't move to entry at TP5). These signals need room to breathe — premature SL tightening kills the runner."},
              {t:"TP5 hit after 24h+",c:C.orange,
                r:"EXIT 100% regardless of everything. The momentum is dead. -15% SL is meaningless — just close at market."},
              {t:"BTC changes to dumping mid-trade",c:C.red,
                r:"Immediately set SL to -5% from CURRENT price (not entry) on all open trades. Override the normal schedule."},
              {t:"Price went -10% before TP5 then recovered",c:C.yellow,
                r:"Score ≥4 at TP5 → the recovery is strong, hold normally. Score ≤2 → exit at TP5, it struggled to get here."},
            ].map((r,i)=>(
              <div key={i} style={{padding:"10px 0",borderBottom:i<3?`1px solid ${C.border}`:"none"}}>
                <div style={{fontWeight:700,fontSize:13,color:r.c,marginBottom:4}}>{r.t}</div>
                <div style={{fontSize:12,color:C.muted,lineHeight:1.6}}>{r.r}</div>
              </div>
            ))}
          </div>
        </div>)}

        {/* ── TIME LIMITS ── NEW ── */}
        {tab===7&&(<div>
          <SectionTitle n="08" title="Time Limits — NEW Addition" sub="The missing piece. How long to wait at each level before giving up on the next TP." />
          <Box color={C.green}>
            <div style={{fontSize:13,color:C.muted,lineHeight:1.7}}>
              <strong style={{color:C.green}}>Why time limits matter:</strong> Once you hold past a TP, your position sits open using
              margin. If the next TP is never coming, you're blocking capital that could be in a fresh signal.
              These limits are from real data — the 90th percentile of all continuations.
            </div>
          </Box>
          <div style={{background:C.s1,border:`1px solid ${C.border}`,borderRadius:5,overflow:"hidden",marginBottom:12}}>
            <div style={{background:C.s2,padding:"9px 16px",display:"grid",
              gridTemplateColumns:"100px 80px 80px 80px 1fr",...mono,
              fontSize:10,color:C.muted,gap:8,letterSpacing:1}}>
              <span>TRANSITION</span><span>75th %ile</span><span>90th %ile</span><span>TIME LIMIT</span><span>MEANING</span>
            </div>
            {[
              {tr:"TP5 → TP10",p75:"15.5h",p90:"53.3h",limit:"48h",meaning:"89% of TP10 hits arrive within 48h of TP5. After 48h: only 11% left.",color:C.green},
              {tr:"TP10 → TP20",p75:"30.0h",p90:"76.5h",limit:"72h",meaning:"89% of TP20 hits arrive within 72h of TP10.",color:C.blue},
              {tr:"TP20 → TP30",p75:"17.8h",p90:"37.9h",limit:"48h",meaning:"96% of TP30 hits arrive within 48h of TP20. Very tight.",color:C.purple},
              {tr:"TP30 → TP50",p75:"29.8h",p90:"49.1h",limit:"48h",meaning:"90% of TP50 hits arrive within 48h of TP30.",color:C.orange},
              {tr:"TP50 → TP75",p75:"16.6h",p90:"27.5h",limit:"48h",meaning:"97% of TP75 hits arrive within 48h of TP50.",color:C.yellow},
              {tr:"TP75 → TP100",p75:"6.8h",p90:"19.2h",limit:"24h",meaning:"95% of TP100 hits arrive within 24h of TP75. Very fast.",color:C.green},
            ].map((r,i)=>(
              <div key={i} style={{display:"grid",gridTemplateColumns:"100px 80px 80px 80px 1fr",
                padding:"10px 16px",borderTop:`1px solid ${C.border}`,gap:8,
                background:i%2===0?C.s1:C.s2,alignItems:"center"}}>
                <span style={{...mono,fontSize:12,color:r.color,fontWeight:700}}>{r.tr}</span>
                <span style={{...mono,fontSize:11,color:C.muted}}>{r.p75}</span>
                <span style={{...mono,fontSize:11,color:C.muted}}>{r.p90}</span>
                <span style={{...mono,fontSize:13,fontWeight:800,color:C.orange}}>{r.limit}</span>
                <span style={{fontSize:11,color:C.muted}}>{r.meaning}</span>
              </div>
            ))}
          </div>
          <Box color={C.orange}>
            <div style={{fontSize:13,color:C.muted,lineHeight:1.8}}>
              <strong style={{color:C.orange}}>How to use these in practice:</strong><br/>
              When TP5 hits at 2:00 PM → set a reminder for 2:00 AM (48h later).<br/>
              If no TP10 alert by then → exit remaining at market. No exceptions.<br/>
              This rule alone prevents "zombie trades" that sit open for days going nowhere,
              blocking margin that could be in a live signal.
            </div>
          </Box>
        </div>)}

        {/* ── CHEATSHEET ── */}
        {tab===8&&(<div>
          <SectionTitle n="09" title="Complete Cheatsheet" sub="Everything on one page." />

          <div style={{background:C.s1,border:`1px solid ${C.green}40`,borderRadius:5,padding:"16px",marginBottom:12}}>
            <div style={{...mono,fontSize:10,color:C.green,letterSpacing:2,marginBottom:12}}>ENTRY CHECKLIST</div>
            {[
              ["BTC trending","= ranging only"],["Quality Score","≥ 3, avoid 7-8"],
              ["Market Cap","< $200M (ideal <$50M)"],["Funding rate","0% to 0.15%"],
              ["OI change","5% to 50%"],["Vol 24h","≥ $5M (Premium ≥ $20M)"],
              ["Entry slippage","within 1% of signal price"],["First action","set SL at -15% immediately"],
            ].map(([l,v],i)=>(
              <div key={i} style={{display:"flex",gap:12,padding:"6px 0",
                borderBottom:`1px solid ${C.border}`,fontSize:12}}>
                <span style={{color:C.green,flexShrink:0,width:16}}>✓</span>
                <span style={{color:C.muted,flex:1}}>{l}</span>
                <span style={{...mono,fontWeight:700,color:C.text}}>{v}</span>
              </div>
            ))}
          </div>

          <div style={{background:C.s1,border:`1px solid ${C.border}`,borderRadius:5,overflow:"hidden",marginBottom:12}}>
            <div style={{background:C.s2,padding:"9px 14px",...mono,fontSize:10,color:C.muted,letterSpacing:2}}>
              SCORE → ACTION AT EACH TP (QUICK REFERENCE)
            </div>
            {[
              {tp:"TP5",sl:"→ Entry",scores:[
                {s:"0-1",a:"EXIT 100%",c:C.red},
                {s:"2-3",a:"Close 60%",c:C.orange},
                {s:"4-5",a:"Close 30%",c:C.blue},
                {s:"6+",a:"Close 10%",c:C.green},
              ],special:"24h+ hit → EXIT 100% regardless",clock:"48h clock starts"},
              {tp:"TP10",sl:"→ +5%",scores:[
                {s:"0-1",a:"EXIT all",c:C.red},
                {s:"2-3",a:"Close 50%",c:C.orange},
                {s:"4-5",a:"Close 20%",c:C.blue},
                {s:"6+",a:"Close 10%",c:C.green},
              ],special:null,clock:"72h clock starts"},
              {tp:"TP20",sl:"→ +12%",scores:[
                {s:"0-2",a:"Close 50%",c:C.orange},
                {s:"3-5",a:"Close 25%",c:C.blue},
                {s:"6-8",a:"Close 10%",c:C.green},
              ],special:"NEW — scoring added here",clock:"48h clock starts"},
              {tp:"TP30",sl:"→ Trail -10%",scores:[
                {s:"0-2",a:"EXIT all",c:C.red},
                {s:"3-5",a:"Close 30%",c:C.blue},
                {s:"6-8",a:"Trail only",c:C.green},
              ],special:"NEW — speed reversal. Slow=better here",clock:"48h clock starts"},
              {tp:"TP50",sl:"→ Trail -8%",scores:[{s:"—",a:"Close 30%, trail rest",c:C.orange}],
                special:"No scoring — trail only",clock:"48h clock starts"},
              {tp:"TP75",sl:"→ Trail -8%",scores:[{s:"—",a:"Close 30%, trail rest",c:C.blue}],
                special:"No scoring — trail only",clock:"24h clock starts"},
              {tp:"TP100",sl:"→ Trail -8%",scores:[{s:"—",a:"Close 50%, trail 50%",c:C.green}],
                special:"Let remaining ride",clock:"2 red 4h candles → exit"},
            ].map((r,i)=>(
              <div key={i} style={{padding:"10px 14px",borderTop:`1px solid ${C.border}`,
                background:i%2===0?C.s1:C.s2}}>
                <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap",marginBottom:6}}>
                  <span style={{fontWeight:800,fontSize:14,color:C.green,...mono,width:50}}>{r.tp}</span>
                  <span style={{...mono,fontSize:11,background:C.orange+"15",border:`1px solid ${C.orange}30`,
                    borderRadius:2,padding:"2px 7px",color:C.orange}}>SL {r.sl}</span>
                  <span style={{...mono,fontSize:11,color:C.muted}}>⏱ {r.clock}</span>
                  {r.special&&<span style={{fontSize:11,color:C.yellow,fontStyle:"italic"}}>{r.special}</span>}
                </div>
                <div style={{display:"flex",gap:5,flexWrap:"wrap"}}>
                  {r.scores.map((sc,j)=>(
                    <div key={j} style={{background:sc.c+"15",border:`1px solid ${sc.c}40`,borderRadius:3,
                      padding:"3px 9px",fontSize:11,...mono,color:sc.c}}>
                      {sc.s} → {sc.a}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <Box color={C.red}>
            <div style={{fontSize:13,color:C.muted,lineHeight:1.9}}>
              <strong style={{color:C.red}}>Hard rules — never break:</strong><br/>
              1. SL at -15% set immediately at entry, before anything else<br/>
              2. SL only moves UP — never down, never wider<br/>
              3. BTC not ranging → skip signal entirely<br/>
              4. TP5 hit after 24h+ → exit 100%, ignore score<br/>
              5. Time limit expired → exit regardless of how good it looks<br/>
              6. Never use above 10x leverage with this strategy
            </div>
          </Box>
        </div>)}

      </div>
    </div>
  );
}
