<!-- kind: reference -->
[PowerPlan docs](README.md) › Reference › Grid tariffs

# Grid tariffs

Your grid company's tariff sets the capacity steps PowerPlan keeps you under and the grid's part of every hour's price. This page lists the grid companies PowerPlan fetches a tariff for, country by country, and what to do where it cannot.

## How PowerPlan gets your tariff

During setup, **Which grid company do you have?** lists the grid companies of your country's sources. Pick yours, and PowerPlan fetches its tariff and asks you to check what the source leaves out. It fetches again once a month, and **Refresh the grid tariff** fetches it now.

Where no source covers your grid company, setup offers your country's template instead. You type the figures from your latest grid bill. Where there is no template either, you type the tariff in by hand.

PowerPlan keeps only the tariff it reads. Everything else it downloads is dropped as soon as the tariff is taken.

## Countries

<!-- generated:begin countries · tools/docs.py writes this block; change the country or source registry, not this table -->
| Country | Grid tariff fetched from | VAT | Setup also asks |
|---|---|---|---|
| Austria | – | 20 % | your region, where its taxes differ; your grid tariff, typed in |
| Australia | Consumer Data Right | 10 % | nothing more |
| Belgium | CWaPE and Brugel, Vlaamse Nutsregulator | 6 % | your postcode |
| Bulgaria | – | 20 % | your grid tariff, typed in |
| Switzerland | ElCom | 8.1 % from 2024-01-01 | your postcode |
| Cyprus | – | 9 %; 19 % from 2027-04-01 | your grid tariff, typed in |
| Czechia | – | 21 % | your grid tariff, typed in |
| Germany | – | 19 % | your region, where its taxes differ; your grid tariff, typed in |
| Denmark | elpris.dk (Forsyningstilsynet) | 25 % | nothing more |
| Estonia | – | 24 % | your grid tariff, typed in |
| Spain | Red Eléctrica (ESIOS) | 21 %; 10 % from 2026-08-01, to 10 kW; 21 % from 2026-10-01 | your region, where its taxes differ |
| Finland | – | 25.5 % | your postcode; your grid tariff, typed in |
| France | – | 20 % | your region, where its taxes differ; the grid tariff from your bill |
| United Kingdom | – | 5 %; 0 % from 2026-10-01; 5 % from 2027-04-01 | your region, where its taxes differ; the grid tariff from your bill |
| Greece | – | 6 % | your region, where its taxes differ; your grid tariff, typed in |
| Croatia | – | 13 % | your grid tariff, typed in |
| Hungary | – | 27 % | your grid tariff, typed in |
| Ireland | – | 9 % | the grid tariff from your bill |
| Iceland | – | 24 % | your grid tariff, typed in |
| Italy | – | 10 % | the grid tariff from your bill |
| Lithuania | – | 21 % | your grid tariff, typed in |
| Luxembourg | – | 8 % | your grid tariff, typed in |
| Latvia | – | 21 % | your grid tariff, typed in |
| Malta | – | 5 % | your grid tariff, typed in |
| Netherlands | – | 21 % | the grid tariff from your bill |
| Norway | Fri Nettleie | 25 % | your postcode; your region, where its taxes differ |
| Poland | TAURON Dystrybucja | 23 % | nothing more |
| Portugal | – | 23 % | your region, where its taxes differ; the grid tariff from your bill |
| Romania | ANRE | 21 % | nothing more |
| Sweden | Eltariff, Energimarknadsinspektionen | 25 % | your region, where its taxes differ |
| Slovenia | – | 22 % | your grid tariff, typed in |
| Slovakia | Západoslovenská distribučná | 19 % | nothing more |
| United States | OpenEI (NREL) | asked | your postcode |
<!-- generated:end countries -->

VAT and national taxes come with PowerPlan, by date and by region, so you never type them in.

## Grid companies by country

Each section names the source, the grid companies it covers, and what you add yourself.

<a name="fri_nettleie"></a>
### Fri Nettleie

Norway. Every grid company in [Fri Nettleie](https://github.com/kraftsystemet/fri-nettleie), cabin tariffs, weekly peaks, and tariffs by main fuse included. Where the source lacks a detail, or was last checked over a year ago, setup asks you to check it against your bill. Your postcode settles which taxes apply where you live.

<a name="eltariff"></a>
### Eltariff

Sweden. The grid companies that publish their tariffs through the industry's Eltariff API. Tariffs with a day and a night power price are both billed. Setup asks whether you pay the lower energy tax in the north.

<a name="ei_household"></a>
### Energimarknadsinspektionen

Sweden. Every other grid company, from the regulator's yearly household figures. Check the result against your bill, since the regulator publishes one typical tariff per company.

<a name="elpris_dk"></a>
### elpris.dk (Forsyningstilsynet)

Denmark. Every grid area, with next season's tariff as soon as your grid company registers it.

<a name="cwape"></a>
### CWaPE and Brugel

Belgium: Wallonia and Brussels. Every grid company, found by your postcode, for a single-rate or a dual-rate meter. For a dual-rate meter, setup asks your day hours.

<a name="vreg_xlsx"></a>
### Vlaamse Nutsregulator

Belgium: Flanders. The eight Fluvius areas, for a digital or an analogue meter, from the regulator's yearly tariff sheet.

<a name="elcom"></a>
### ElCom

Switzerland. Every municipality's grid operator, found by your postcode, for your household category (H1 to H8). ElCom publishes one average price per category, so a day and a night rate are not told apart.

<a name="anre"></a>
### ANRE

Romania. The eight distribution areas, each named with its counties.

<a name="tauron"></a>
### TAURON Dystrybucja

Poland. TAURON Dystrybucja's G11, G12, and G12w. Setup asks your phases and your yearly consumption group. PGE, Enea, Energa, and Stoen publish no tariff PowerPlan can read yet, so use the template there.

<a name="zsdis"></a>
### Západoslovenská distribučná

Slovakia: the west. The switching times of every household tariff program, the code on your meter's display (0.2.2). Setup asks your high and low grid price from your bill. An appliance the grid switches on its own code gets no power outside that code's windows.

<a name="esios"></a>
### Red Eléctrica (ESIOS)

Spain. The national 2.0TD tolls and charges, the same for every distributor, by hour. Setup asks your contracted power for P1 and P2.

<a name="openei_urdb"></a>
### OpenEI (NREL)

United States. Every utility serving your ZIP code, with its current rates and their demand charges by season.

<a name="cdr_energy"></a>
### Consumer Data Right

Australia. Every retailer on the national register, and its residential plans for your postcode. Setup asks how the plan's demand charge is measured.

### Countries with a template

Italy, Portugal, France, Ireland, the Netherlands, and the United Kingdom have a template: setup asks the few figures on your bill.

### Countries without a source yet

Czechia, Austria, Germany, Slovenia, and the rest of the table above without a source: type your tariff in by hand. In Finland, your postcode names your grid company first. ČEZ Distribuce's switching times sit behind a code check that PowerPlan does not get around.

## An appliance with its own grid tariff

Some grid tariffs apply to one appliance, such as a heat pump under §14a Modul 3 or an appliance the grid switches. When your grid company's tariff has one, the appliance's last setup step asks **Own meter or grid tariff** and **Switched by the grid**. The appliance is then planned and billed on its own tariff.

## Sources and credits

<!-- generated:begin sources · tools/docs.py writes this block; change the source registry, not this table -->
| Source | Country | Where the prices come from | Key |
|---|---|---|---|
| ANRE | Romania | the regulator's or the country's own open data | `anre` |
| Consumer Data Right | Australia | the regulator's or the country's own open data | `cdr_energy` |
| CWaPE and Brugel | Belgium | the regulator's or the country's own open data | `cwape` |
| Energimarknadsinspektionen | Sweden | the regulator's published tariff sheet | `ei_household` |
| ElCom | Switzerland | the regulator's or the country's own open data | `elcom` |
| elpris.dk (Forsyningstilsynet) | Denmark | the regulator's or the country's own open data | `elpris_dk` |
| Eltariff | Sweden | the regulator's or the country's own open data | `eltariff` |
| Red Eléctrica (ESIOS) | Spain | the regulator's or the country's own open data | `esios` |
| Fri Nettleie (CC BY 4.0) | Norway | a national data service | `fri_nettleie` |
| OpenEI (NREL) | United States | the regulator's or the country's own open data | `openei_urdb` |
| TAURON Dystrybucja | Poland | the grid company's own website | `tauron` |
| Vlaamse Nutsregulator | Belgium | the regulator's published tariff sheet | `vreg_xlsx` |
| Západoslovenská distribučná | Slovakia | the grid company's own website | `zsdis` |
<!-- generated:end sources -->

**See also:** [Actions](actions.md), [Glossary](glossary.md).
