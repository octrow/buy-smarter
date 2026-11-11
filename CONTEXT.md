# buy-smarter

An explainable shopping assistant for Kazakhstan: it ranks offers from Kaspi, Ozon, OLX and Wildberries for one user's budget, city and must-haves, and says what it could not verify.

## Language

**Local store**:
The single per-user database (`buysmarter.db` in the user data dir) holding price history, offer snapshots, preferences and learned category knowledge. The LLM and Kimovil caches are separate and not part of it.
_Avoid_: local DB, cache, fallback file

**Category knowledge**:
What the assistant has learned about one product category: its brand book, model book, category type and, for composition-driven categories, its ingredient book. Kept in the local store; a query whose category has none inherits from a parent profile or falls back to spec-driven.
_Avoid_: category cache, category config

**Category type** (legacy):
How a category was judged before capabilities; still read and written for older records, and becomes the composition capability. **Spec-driven** (the default — phones, shoes, appliances): offers are compared on specs, brand and price. **Composition-driven** (medicines, cosmetics, supplements): the active substances in the composition decide, so search and ranking also work by substance.
_Avoid_: category kind, mode

**Learning a category**:
Building category knowledge for a category that has none, from web research or the user's notes. The user starts it (`learn-category`, `learn`, the report's button), or it starts itself on the first query for a category with no books anywhere (auto-learning). The desktop app and the CLI learn before that query's search and then search with the books (the user may search without waiting); MCP learns after the report, the API only when switched on.
_Avoid_: training, category creation

**Category profile**:
Everything the assistant knows about how to judge one product category: its brand and model books and the capabilities switched on for it, each with its own parameters. Category knowledge is the part of the profile learned so far.
_Avoid_: category config, category settings

**Capability**:
One optional way of judging offers, switched on either by the category profile (a category capability) or by the query's own words (a request capability): composition (active substances), benchmark (a performance index such as Kimovil), audience, size, counterfeit checks, official stores, used market. A capability can add searches, enrich candidates, reject candidates, score an axis, add pros and cons, or add a report section. The core flow is the same for every category; capabilities are what differ. A category capability's automatic value comes from learning, Jev or a rule; the user's own choice (`user`: on, off or not set) wins over it, and no automatic writer ever changes it.
_Avoid_: module (that word means a code module), plugin, trait, feature (that word means a required product property)

**Category capability** / **Request capability**:
The two levels of capabilities. A category capability belongs to the category profile and holds for every query in that category (composition, benchmark, counterfeit checks, official stores, used market, cross-market comparison). Official stores and the used market are on until learning or the user switches them off; a used query ("б/у ноутбук") keeps OLX whatever the used market switch says. A request capability comes from one query's own words and holds only for it (audience, size and dimensions, compatibility with a device, excluded substances, original only, used only, delivery deadline, country of origin). Both plug into the core the same way.
_Avoid_: global capability, query feature

**Restricted category**:
Goods whose sale or delivery is limited (prescription medicines, alcohol, tobacco and vapes, weapons). The assistant still searches and warns before the results that buying or delivery may be restricted; it does not refuse.
_Avoid_: banned, forbidden

**Update channel**:
Where updates come from: `code` takes every new commit on GitHub's main branch, `releases` only tagged releases. A git checkout follows `code` by default, every other install `releases`; the frozen desktop build can follow only releases.
_Avoid_: branch (that is git's word), update source

**Request intent**:
What kind of request the user made, decided before any search. **Product**: names the goods ("мужские кроссовки 42"). **Need**: names a problem the goods solve ("шампунь от перхоти"); the need is passed on to capabilities. **Open**: names no goods ("подарок для мамы") and needs a clarification. **Multi**: names several goods to compare or buy together ("iPhone 15 или S24", "всё для новорождённого"); in v1 the user picks one to start with. **Service**: asks for a service, not goods ("ремонт телефона"); no search. **Unclear**: cannot be read; the user is asked to rephrase. Whether the goods are a restricted category is a separate flag, not an intent.
_Avoid_: query type, mode

**Clarification**:
A question asked before any search when the request is ambiguous (open, a need several product types solve, several products named, a service, an unreadable query, or a capability missing a parameter), with ready answers to pick from. At most two rounds; on the last one the first option is searched and the answer says what was assumed. Never asked for a request that can be searched as is.
_Avoid_: follow-up, dialog

**Search phrase**:
The words actually sent to the marketplaces ("мужские кроссовки", "крем с адапаленом"): the product type plus the audience, model, substance or theme the user named. Substances and relevance are read from it.
_Avoid_: category (the old field name), query (that is the user's whole text)

**Product type**:
The short canonical name of the goods a query is about ("кроссовки", "шампунь", "зарядка"), without audience, size, compatibility or theme, which belong to the request. It is the key of a category profile. The words actually sent to the marketplaces are the search phrase, which may carry more.
_Avoid_: category string, search query

**Parent profile**:
A broader category profile that a narrower one inherits from ("крем от акне" → "косметика"): books and capabilities come from the parent unless the child sets its own. Knowledge learned while using inherited books is saved to the profile that owns them.
_Avoid_: related category, fallback category

**Delivery route**:
How an offer reaches the user: the marketplace's courier or parcel locker, a marketplace pickup point, direct international shipping, or cargo (a forwarder with a Chinese address, priced per kilogram or by volume). Each route has its own cost, time and effort. One offer can have several routes; it is shown with the route that gives the lowest landed cost, the others in the breakdown.
_Avoid_: shipping method, delivery difficulty (effort is a property of the route)

**Landed cost**:
What the user actually pays to get an offer to the door: price plus delivery, import VAT or duty, marketplace fee and currency conversion loss, shown with each part. Computed per delivery route.
_Avoid_: total price, final price

**Authenticity level**:
How sure the assistant is that an offer is the genuine product: **certainly original** (the brand's own site, in any country, or its flagship store on a marketplace), **likely original** (an authorized or official seller, or a low-counterfeit-risk brand, with no counterfeit signal), **unclear** (no counterfeit signal and no proof either way), **likely fake** (any counterfeit signal). A likely fake gets no role; "original only" hides likely fakes and nothing else.
_Avoid_: suspected fake flag (the old yes/no), genuine score

**Brand counterfeit risk**:
How often a brand is faked on marketplaces: high (Adidas, Nike, Levi's), normal, or low (small brands nobody copies). Part of the brand book, learned with the category and overridable by the user. Low risk lifts an offer with no counterfeit signal to likely original; high risk warns on every offer the brand does not sell itself and lets no outlet or sale word excuse a deep gap below the original's price. Price alone is weak evidence: outlets, sales, clearance and other markets sell genuine goods cheap.
_Avoid_: brand tier (that is brand quality, not how often it is faked)

**Cross-market comparison**:
A category capability that also looks beyond Kaspi, Ozon and OLX (Wildberries, brands' own sites in China and elsewhere, Taobao, Tmall, Pinduoduo) and compares offers by landed cost, delivery route and authenticity level. Learning switches it on for clothes and shoes; for other categories the query's words switch it on ("где дешевле", "сравни площадки", "из Китая").
_Avoid_: multi-market search, China mode

**Body measurements**:
The user's height, weight and chest, waist and inside-leg measurements, kept in the local store only. Used to fill seller questions on the user's own screen (the terminal, the app); a saved report, MCP and the HTTP API show blanks in their place.
_Avoid_: size profile (a size is the label on the goods, not the body)

**Seller question**:
A ready message in Chinese and English to send a seller before buying from a Chinese source, filled with the user's body measurements: the size chart in centimetres, the parcel weight, proof of authenticity. Shown only when the size, weight or authenticity of a Chinese offer is not settled by its page.
_Avoid_: seller template, chat script
