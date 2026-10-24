# buy-smarter

An explainable shopping assistant for Kazakhstan: it ranks offers from Kaspi, Ozon and OLX for one user's budget, city and must-haves, and says what it could not verify.

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
The two levels of capabilities. A category capability belongs to the category profile and holds for every query in that category (composition, benchmark, counterfeit checks, official stores, used market). Official stores and the used market are on until learning or the user switches them off; a used query ("б/у ноутбук") keeps OLX whatever the used market switch says. A request capability comes from one query's own words and holds only for it (audience, size and dimensions, compatibility with a device, excluded substances, original only, used only, delivery deadline, country of origin). Both plug into the core the same way.
_Avoid_: global capability, query feature

**Restricted category**:
Goods whose sale or delivery is limited (prescription medicines, alcohol, tobacco and vapes, weapons). The assistant still searches and warns before the results that buying or delivery may be restricted; it does not refuse.
_Avoid_: banned, forbidden

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
