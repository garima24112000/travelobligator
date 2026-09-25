# Section 202C — human itinerary review (202c_as_configured)

Reviewer: Claude (AI assistant) acting as QA reviewer under the 202C rubric. Every itinerary was read place by place against its saved provider tags. The YES/MAYBE/NO labels are a preference PROXY, not the product owner's own preference -- the owner should confirm or override them. PASS/WARN/FAIL: PASS = sound for its request; WARN = usable with notable weaknesses; FAIL = materially unsuitable for its request (requested interest served by junk objects, or plan dominated by low-value/unsuitable stops, or a requested interest impossible to serve while the plan is also thin). No numeric score is computed.

PASS/WARN/FAIL = itinerary quality; YES/MAYBE/NO = would I use it as a starting point. Labels are a preference proxy, not the product owner's own; no numeric score is computed. No secrets, prompts or raw provider payloads are included.

## LIS-1 — Lisbon, Portugal

- resolved destination: Lisbon, Portugal
- requested interests: food, history | pace: balanced | days: 3
- narrator: deterministic_fallback | AI proposal: rejected | places provider: success | routing: success | latency: 75.1s
- interests matched by scheduled places: food, history | quality findings: none
- Day 1: Mercado da Ribeira [food_market → food]; Tamareira-do-Senegal [landmark → history]; Pelourinho de Lisboa [landmark → history]
- Day 2: Museu Geológico [museum → history]; Museu da Marioneta [museum → history]; Tipuana [landmark → history]
- Day 3: Museu do Aljube - Resistência e Liberdade [museum → history]; Mercado de Santa Clara [food_market → food]; Museu Bordalo Pinheiro [museum → history]
- **WARN / MAYBE** — Museums + two real food markets, compact days (<= 0.8 km on days 1-2). But two of the 'history' stops are single heritage TREES (Tamareira-do-Senegal, Tipuana: provider tag natural=tree) and Belem / Alfama / Castelo are absent because the AI proposal path did not run (rejected). Grounded, honest, usable as a skeleton.

## LIS-2 — Lisbon, Portugal

- resolved destination: Lisbon, Portugal
- requested interests: architecture, nightlife | pace: packed | days: 4
- narrator: ai | AI proposal: rejected | places provider: success | routing: success | latency: 39.1s
- interests matched by scheduled places: architecture, nightlife | quality findings: none
- Day 1: Pelourinho de Lisboa [landmark → architecture]; Tamareira-do-Senegal [landmark → architecture]; Teatro Nacional de São Carlos [entertainment]; Leitaria A Camponeza [historic → architecture]
- Day 2: Hot Club Portugal [nightlife → nightlife]; Miradouro de São Pedro de Alcântara [viewpoint]; Museu Maçónico Português [museum]; Museu Geológico [museum]
- Day 3: Tipuana [landmark → architecture]; Museu da Marioneta [museum]; Mercado da Ribeira [food_market]; Animatógrafo do Rossio [historic → architecture]
- Day 4: Ginkgo [landmark → architecture]; Museu do Aljube - Resistência e Liberdade [museum]; Museu do Centro Científico e Cultural de Macau [museum]; Museu Bordalo Pinheiro [museum]
- **FAIL / NO** — 'Architecture' is claimed as served by three single heritage trees (Tamareira-do-Senegal, Tipuana, Ginkgo) and a dairy shop (Leitaria A Camponeza). Nightlife is genuinely served once (Hot Club Portugal, amenity=nightclub) and the theatre is correctly NOT counted as nightlife. Day 4 is three museums plus a tree. A traveler asking for architecture would be misled by the interest-match labels.

## NYC-1 — New York City, USA

- resolved destination: New York, United States
- requested interests: museums, food | pace: balanced | days: 4
- narrator: deterministic_fallback | AI proposal: rejected | places provider: success | routing: success | latency: 62.9s
- interests matched by scheduled places: food, museum | quality findings: none
- Day 1: Yeshiva University Museum [museum → museum]; Union Square Green Market [food_market → food]; Rubin Museum of Art [museum → museum]
- Day 2: Lower East Side Tenement Museum [museum → museum]; SoHo [landmark]; South Street Seaport [landmark]
- Day 3: Skyscraper Museum [museum → museum]; Stonewall Inn [landmark]; Gagosian [museum → museum]
- Day 4: Museum of Sex [museum → museum]; The Morgan Library & Museum [museum → museum]; Bryant Park [landmark]
- **WARN / MAYBE** — Nine museum/landmark stops that do fit 'museums, food' (Tenement Museum, Rubin, Morgan Library, Skyscraper Museum, Union Square Green Market) and no cross-borough outlier (unlike 202A). But Gagosian is a commercial gallery, Museum of Sex is an odd headline stop, and the Met / MoMA / AMNH / Central Park (present in 202A via AI proposals) are absent.

## NYC-2 — New York City, USA

- resolved destination: New York, United States
- requested interests: architecture, parks | pace: relaxed | days: 3
- narrator: ai | AI proposal: rejected | places provider: success | routing: success | latency: 39.2s
- interests matched by scheduled places: architecture, outdoors | quality findings: none
- Day 1: Stonewall Inn [landmark → architecture]; Yeshiva University Museum [museum]
- Day 2: Bryant Park [landmark → architecture/outdoors]; SoHo [landmark → architecture]
- Day 3: One World Observatory [viewpoint → outdoors]; Lower East Side Tenement Museum [museum]
- **WARN / MAYBE** — Relaxed 2 stops/day is respected and days are coherent (Bryant Park + SoHo, One World Observatory + Tenement Museum). Was PASS in 202A (Central Park + High Line, via AI proposal). 'Stonewall Inn' matched 'architecture' via its heritage tag, which is a stretch. Reasonable starting point; needs Central Park added by hand.

## DC-1 — Washington, DC, USA

- resolved destination: Washington, District of Columbia, United States
- requested interests: history, museums | pace: balanced | days: 3
- narrator: deterministic_fallback | AI proposal: rejected | places provider: fallback_used | routing: success | latency: 120.2s
- interests matched by scheduled places: history | quality findings: interest_supply_limited
- Day 1: Lincoln Memorial [park_nature]; 56 Signers of the Declaration of Independence Memorial [landmark → history]; Bernardo de Galvez [landmark → history]
- Day 2: Dupont Circle [landmark → history]; Hahnemann Monument [landmark → history]; National Law Enforcement Officers Memorial [landmark → history]
- Day 3: Capitol Hill [landmark → history]; American Legion Freedom Bell [landmark → history]; Tomb of the Unknown Soldier [landmark → history]
- **WARN / MAYBE** — Places provider used the per-tag fallback: 'museums' has NO supply (honest interest_supply_limited) so the plan is memorials only (Lincoln Memorial, Signers memorial, Freedom Bell, Law Enforcement Memorial). Includes the Tomb of the Unknown Soldier, which the provider places in Arlington (outside DC), and neighbourhood/traffic-circle 'landmarks' (Dupont Circle, Capitol Hill). No Smithsonian museum at all -- materially incomplete for a first-time DC museums+history trip, but the limitation is disclosed.

## DC-2 — Washington, DC, USA

- resolved destination: Washington, District of Columbia, United States
- requested interests: food, outdoors | pace: relaxed | days: 2
- narrator: ai | AI proposal: rejected | places provider: fallback_used | routing: success | latency: 71.1s
- interests matched by scheduled places: none | quality findings: interest_supply_limited
- Day 1: National Guard Memorial Museum [museum]; Howard University Museum [museum]
- Day 2: All Hallows Guild Traveling Carousel [landmark]; Dupont Circle [landmark]
- **FAIL / NO** — Both requested interests (food, outdoors) have no supply and are reported as such; the two-day plan is National Guard Memorial Museum, Howard University Museum, a traveling carousel and Dupont Circle -- none relevant to food/outdoors and no National Mall / market. Honest, but not a useful itinerary. (The 202A hospital stop is gone.)

## SF-1 — San Francisco, USA

- resolved destination: San Francisco, California, United States
- requested interests: outdoors, food | pace: packed | days: 4
- narrator: ai | AI proposal: rejected | places provider: fallback_used | routing: success | latency: 106.3s
- interests matched by scheduled places: outdoors | quality findings: interest_supply_limited
- Day 1: Hawk Hill [park_nature → outdoors]; Golden Gate Bridge Vista Point [viewpoint → outdoors]; Bay Area Discovery Museum [museum]; Point Bonita Viewpoint [viewpoint → outdoors]
- Day 2: Musée Mécanique [museum]; Truhlsen-Marmor Museum of the Eye [museum]; Dragon Gate [landmark]; Exploratorium [museum]
- Day 3: Inspiration Point [viewpoint → outdoors]; California Society of Pioneers Museum and Library [museum]; Alamo Square Historic District [landmark]; Painted Ladies [landmark]
- Day 4: Lands End Point [viewpoint → outdoors]; Christmas Tree Point [viewpoint → outdoors]; San Francisco Railway Museum [museum]; Bird Island Overlook [viewpoint → outdoors]
- **WARN / MAYBE** — 202A's empty day 4 is fixed ([4,4,4,4]) and Golden Gate Bridge Vista Point / Painted Ladies / Lands End appear; outdoors is served by 7 viewpoint/park stops. But Day 1 pairs Marin-side places (Hawk Hill, Point Bonita, Bay Area Discovery Museum) with the bridge, day 4 spans 12.7 km, 'food' is supply-limited, and museums fill the rest.

## SF-2 — San Francisco, USA

- resolved destination: San Francisco, California, United States
- requested interests: architecture, history | pace: relaxed | days: 3
- narrator: ai | AI proposal: rejected | places provider: success | routing: success | latency: 62.0s
- interests matched by scheduled places: architecture, history | quality findings: none
- Day 1: Conservatory of Flowers [landmark → architecture/history]; Alamo Square Historic District [landmark → architecture/history]
- Day 2: GLBT History Museum [museum → history]; Dragon Gate [landmark → architecture/history]
- Day 3: Musée Mécanique [museum → history]; Exploratorium [museum → history]
- **WARN / MAYBE** — Relaxed pace respected (2 stops/day), all in-city, Conservatory of Flowers / Alamo Square / GLBT History Museum / Exploratorium. Interest matching is generous (a science museum and an arcade museum count as 'history'). Coherent and modest.

## CHI-1 — Chicago, USA

- resolved destination: Chicago, South Chicago Township, Cook County, Illinois, United States
- requested interests: architecture, food | pace: balanced | days: 3
- narrator: ai | AI proposal: rejected | places provider: fallback_used | routing: success | latency: 91.6s
- interests matched by scheduled places: architecture | quality findings: interest_supply_limited
- Day 1: Nuclear Energy [landmark → architecture]; White City Amusement Park [landmark → architecture]; Haymarket Police Memorial [landmark → architecture]
- Day 2: Haymarket Memorial [landmark → architecture]; Batcolumn [landmark → architecture]; Home Insurance Building Site [landmark → architecture]
- Day 3: Union Stock Yards Fire Memorial [landmark → architecture]; William McKinley Memorial [landmark → architecture]; Vietnam Veterans Memorial [landmark → architecture]
- **FAIL / NO** — Eight of nine stops are memorials, sculptures or sites (Nuclear Energy sculpture, Batcolumn, Haymarket memorials, Home Insurance Building Site plaque, McKinley/Vietnam memorials, White City abandoned park) all tagged 'architecture'. The Chicago Architecture Center, river, Millennium Park are absent. 'Food' is unserved (disclosed). 202B.2 had 0 low-value stops for this case; the places provider used the fallback query this time.

## CHI-2 — Chicago, USA

- resolved destination: Chicago, South Chicago Township, Cook County, Illinois, United States
- requested interests: museums, nightlife | pace: packed | days: 4
- narrator: deterministic_fallback | AI proposal: rejected | places provider: fallback_used | routing: success | latency: 69.2s
- interests matched by scheduled places: museum | quality findings: interest_supply_limited
- Day 1: Money Museum [museum → museum]; Home Insurance Building Site [landmark]; Begin Route 66 [landmark]; The Museum of Contemporary Photography [museum → museum]
- Day 2: McCormick Bridgehouse & Chicago River Museum [museum → museum]; American Writers Museum [museum → museum]; Chicago Architecture Center [museum → museum]; Richard H. Driehaus Museum [museum → museum]
- Day 3: National Museum of Puerto Rican Arts & Culture [museum → museum]; Ukrainian National Museum [museum → museum]; Polish Museum of America [museum → museum]; Haymarket Memorial [landmark]
- Day 4: Loyola University Museum of Art [museum → museum]; Chicago Children's Museum [museum → museum]; Batcolumn [landmark]; Union Stock Yards Fire Memorial [landmark]
- **WARN / MAYBE** — Eleven real museums across four days (Money Museum, American Writers Museum, Chicago Architecture Center, Driehaus, Children's Museum...) is a genuine improvement on 202A's public-art dominance, but 5 of 16 stops are still filler (Home Insurance Building Site, Begin Route 66 sign, Haymarket Memorial, Batcolumn, Stock Yards memorial). Nightlife has no supply (disclosed). Art Institute / Field / Shedd absent.

## SEA-1 — Seattle, USA

- resolved destination: Seattle, King County, Washington, United States
- requested interests: outdoors, food | pace: relaxed | days: 3
- narrator: deterministic_fallback | AI proposal: rejected | places provider: success | routing: success | latency: 71.7s
- interests matched by scheduled places: outdoors | quality findings: interest_supply_limited
- Day 1: Seattle Center [landmark → outdoors]; Seattle Children's Museum [museum]
- Day 2: Seattle Art Museum [museum]; Museum of History and Industry [museum]
- Day 3: Admiral Way Viewpoint [viewpoint → outdoors]; Swiftsure (LV-83) [landmark]
- **WARN / MAYBE** — Relaxed 2/day and in-city; SAM, MOHAI, Seattle Center, a viewpoint. 'Outdoors' is thinly served (Seattle Center, Admiral Way viewpoint) and 'food' supply-limited (disclosed). Space Needle / Pike Place are not in the provider pool and the AI proposal did not run, so destination-defining attractions are still missing.

## SEA-2 — Seattle, USA

- resolved destination: Seattle, King County, Washington, United States
- requested interests: museums | pace: balanced | days: 3
- narrator: ai | AI proposal: rejected | places provider: success | routing: success | latency: 27.1s
- interests matched by scheduled places: museum | quality findings: none
- Day 1: Seattle Art Museum [museum → museum]; Center for Wooden Boats [museum → museum]; Seattle Children's Museum [museum → museum]
- Day 2: Museum of History and Industry [museum → museum]; Swiftsure (LV-83) [landmark]; Arthur Foss [landmark]
- Day 3: Seattle Asian Art Museum [museum → museum]; Virginia V [landmark]; Henry Art Gallery [museum → museum]
- **PASS / MAYBE** — Museums-only request gets six real museums (Seattle Art Museum, MOHAI, Asian Art Museum, Henry Art Gallery, Center for Wooden Boats, Children's Museum) plus three historic vessels; compact days, no filler objects. Would still want Pike Place / Space Needle added, but the request is served well.

## MIA-1 — Miami, USA

- resolved destination: Miami, Miami-Dade County, Florida, United States
- requested interests: nightlife, food | pace: packed | days: 3
- narrator: ai | AI proposal: rejected | places provider: success | routing: success | latency: 61.6s
- interests matched by scheduled places: food, nightlife | quality findings: none
- Day 1: Club Space [nightlife → nightlife]; Patricia and Phillip Frost Museum of Science [museum]; Pérez Art Museum Miami [museum]; Freedom Tower at Miami Dade College [landmark]
- Day 2: Loft Sofa - Modern Furniture Store [food_market → food]; Venetian Way [landmark]; The Congress Building [landmark]; HistoryMiami Museum [museum]
- Day 3: Villa Vizcaya [museum]; Vizcaya Farmer’s Market [food_market → food]; Daily Bread Marketplace [food_market → food]; 5 Diamond Delicacies [food_market → food]
- **WARN / MAYBE** — Nightlife (Club Space, amenity=nightclub) and food (four 'food market' stops, one of which is the furniture store below) are both represented, a big change from 202A's zero. One of those food stops is 'Loft Sofa - Modern Furniture Store' (OSM mis-tag amenity=marketplace), Day 3 spans ~10 km, and the sub-exhibit problem is gone. Frost Science, PAMM, Vizcaya are sound anchors.

## MIA-2 — Miami, USA

- resolved destination: Miami, Miami-Dade County, Florida, United States
- requested interests: outdoors, architecture | pace: relaxed | days: 4
- narrator: ai | AI proposal: rejected | places provider: success | routing: success | latency: 48.5s
- interests matched by scheduled places: architecture, outdoors | quality findings: none
- Day 1: Miami Circle at Brickell Point [park_nature → outdoors]; The Congress Building [landmark → architecture]
- Day 2: Freedom Tower at Miami Dade College [landmark → architecture]; Pérez Art Museum Miami [museum]
- Day 3: Venetian Way [landmark → architecture]; HistoryMiami Museum [museum]
- Day 4: Villa Vizcaya [museum]; Tower Theater [historic → architecture]
- **WARN / MAYBE** — Relaxed, in-city, 8 stops; architecture served (Freedom Tower, Congress Building, Tower Theater, Venetian Way). 'Outdoors' is served only by Miami Circle (a small archaeological site); no beach / park. Zoo sub-exhibits (202A) are gone.

## LA-1 — Los Angeles, USA

- resolved destination: Los Angeles, Los Angeles County, California, United States
- requested interests: food, outdoors | pace: balanced | days: 4
- narrator: ai | AI proposal: rejected | places provider: success | routing: success | latency: 71.5s
- interests matched by scheduled places: food, outdoors | quality findings: none
- Day 1: Cole's P.E. Buffet [landmark → food]; Angels Flight [landmark]; Museum of Contemporary Art [museum]
- Day 2: Point Grandview [viewpoint → outdoors]; Heritage Square [museum]; LA Plaza De Culturas Y Artes [museum]
- Day 3: Neutra Studio and Residences (VDL Research House) [landmark]; City Hall Observation Deck [viewpoint → outdoors]; Koreatown [landmark]
- Day 4: Institute of Contemporary Art, Los Angeles [museum]; California African American Museum [museum]; Natural History Museum of Los Angeles County [museum]
- **WARN / MAYBE** — 202A's gallery domination is gone (0 flagged). Food is 'served' by Cole's P.E. Buffet (one provider-tagged restaurant), outdoors by two viewpoints; the rest are museums and Angels Flight / Koreatown / Neutra house. Day 3 spread ~5.8 km. Coherent, but thin on both requested interests.

## LA-2 — Los Angeles, USA

- resolved destination: Los Angeles, Los Angeles County, California, United States
- requested interests: museums, history | pace: packed | days: 3
- narrator: deterministic_fallback | AI proposal: rejected | places provider: success | routing: success | latency: 30.1s
- interests matched by scheduled places: history, museum | quality findings: none
- Day 1: Natural History Museum of Los Angeles County [museum → museum/history]; California African American Museum [museum → museum/history]; Shrine Auditorium [landmark → history]; Koreatown [landmark → history]
- Day 2: Neutra Studio and Residences (VDL Research House) [landmark → museum/history]; LA Plaza De Culturas Y Artes [museum → museum/history]; Thien Hau Temple [landmark → history]; Heritage Square [museum → museum/history]
- Day 3: Angels Flight [landmark → history]; Museum of Contemporary Art [museum → museum/history]; El Pueblo de Los Angeles Historical Monument [historic → history]; Institute of Contemporary Art, Los Angeles [museum → museum/history]
- **PASS / YES** — Museums + history, packed: Natural History Museum, CAAM, MOCA, ICA, LA Plaza, Heritage Square, El Pueblo, Angels Flight, Thien Hau Temple, Shrine Auditorium, Koreatown, Neutra house -- 12 stops, all in-city, days 5.5/5.0/2.4 km max (car-oriented city). Requested interests served accurately; no filler objects.

## LIS-BARE — Lisbon

- resolved destination: Lisbon, Portugal
- requested interests: food, history | pace: balanced | days: 3
- narrator: deterministic_fallback | AI proposal: rejected | places provider: success | routing: success | latency: 14.3s
- interests matched by scheduled places: food, history | quality findings: none
- Day 1: Mercado da Ribeira [food_market → food]; Tamareira-do-Senegal [landmark → history]; Pelourinho de Lisboa [landmark → history]
- Day 2: Museu Geológico [museum → history]; Museu da Marioneta [museum → history]; Tipuana [landmark → history]
- Day 3: Museu do Aljube - Resistência e Liberdade [museum → history]; Mercado de Santa Clara [food_market → food]; Museu Bordalo Pinheiro [museum → history]
- **WARN / MAYBE** — Bare 'Lisbon' now resolves to Lisbon, Portugal (202A: empty blocked plan) and returns the same plan as LIS-1 (same two heritage-tree fillers). Localized-destination failure FIXED.

## NYC-BARE — New York City

- resolved destination: New York, United States
- requested interests: museums, food | pace: balanced | days: 3
- narrator: deterministic_fallback | AI proposal: rejected | places provider: success | routing: success | latency: 14.2s
- interests matched by scheduled places: food, museum | quality findings: none
- Day 1: Yeshiva University Museum [museum → museum]; Union Square Green Market [food_market → food]; Rubin Museum of Art [museum → museum]
- Day 2: Lower East Side Tenement Museum [museum → museum]; Stonewall Inn [landmark]; Skyscraper Museum [museum → museum]
- Day 3: Museum of Sex [museum → museum]; The Morgan Library & Museum [museum → museum]; Bryant Park [landmark]
- **WARN / MAYBE** — Works without a country and returns nine stops with no empty day (202A day 3 was empty); same museum-heavy content as NYC-1 (Gagosian, Museum of Sex) and no Met/MoMA/Central Park.

## COR-ES — Cordoba, Spain

- resolved destination: Córdoba, Andalusia, Spain
- requested interests: food, history | pace: balanced | days: 3
- narrator: deterministic_fallback | AI proposal: rejected | places provider: fallback_used | routing: success | latency: 94.0s
- interests matched by scheduled places: food, history | quality findings: none
- Day 1: Zoco Municipal [landmark → food/history]; Museo Taurino de Córdoba [museum → history]; Casa de Sefarad [museum → history]
- Day 2: Centro de Arte Moderno Rafael Botí [museum → history]; Alcázar de los Reyes Cristianos [landmark → history]; Torre de la Calahorra - Museo Vivo de Al-Ándalus [landmark → history]
- Day 3: Mercado Victoria [food_market → food]; Templo romano [landmark → history]; Museo de Julio Romero de Torres [museum → history]
- **WARN / MAYBE** — 202B.2 addition (202A/B.1: resolved but EMPTY plan). Now nine compact stops, resolved as 'Cordoba, Andalusia, Spain', food + history both served (Mercado Victoria, Zoco Municipal, Alcazar de los Reyes Cristianos, Roman temple). The Mezquita-Catedral -- the city's defining site -- is absent (not in the provider pool; AI proposal did not run).
