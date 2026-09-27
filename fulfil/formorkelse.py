"""Generator: personlig ortsguide + affisch för solförmörkelsen 2 augusti 2027.

En PDF per språk: sida 1–3 = guide (A4 stående), sida 4 = affisch (A3 stående).
Beräkning: Skyfield + JPL DE421, topocentriska positioner för sol och måne, egen förmörkelsegeometri
(vinkelavstånd mot skenbara radier, rotsökning för kontakterna, cirkelskärning för täckningsgraden).
Totalitetsbandet på kartan räknas med samma metod över ett rutnät (sparas i data/cache/).
Karta: Natural Earth 1:50m (public domain). Typsnitt: Noto (SIL OFL 1.1). Ingen AI i produkten.

Körning:  python formorkelse.py order.json  -> <ut>/<id>_<språk>.pdf + <ut>/<id>_meta.json
Order:    {"id","text","place","lat","lon","timezone","languages":[...], "elevation_m"?: 0}
Felinjektion (bara för tester): miljövariabeln FELINJEKTION=<namn>, se FEL nedan.
"""
import gzip, hashlib, json, math, os, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from scipy.optimize import brentq, minimize_scalar
from skyfield.api import Loader, wgs84
from skyfield.framelib import itrs
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

GENERATOR_VERSION = "formorkelse/0.1.0"
ROOT = Path(__file__).parent
DATA = ROOT / "data"
FONTS = ROOT / "fonts"
A4 = (210 * mm, 297 * mm)
A3 = (297 * mm, 420 * mm)
K_PEN, K_UMB = 0.272488, 0.272281          # månens radie i jordradier (konvention för yttre/inre kontakt)
R_SUN_KM, R_EQ_KM, FLAT = 696000.0, 6378.137, 1 / 298.257223563
DAY = (2027, 8, 2)
FEL = os.environ.get("FELINJEKTION", "")

pdfmetrics.registerFont(TTFont("Serif", str(FONTS / "NotoSerif-Regular.ttf")))
pdfmetrics.registerFont(TTFont("SerifIt", str(FONTS / "NotoSerif-Italic.ttf")))
pdfmetrics.registerFont(TTFont("Sans", str(FONTS / "NotoSans-Regular.ttf")))

# ------------------------------------------------------------------ färger
BG = (0.035, 0.055, 0.11)
INK = (0.96, 0.93, 0.85)
MUTE = (0.72, 0.70, 0.64)
GOLD = (0.85, 0.79, 0.62)
SUN = (1.0, 0.80, 0.30)
SEA = (0.06, 0.09, 0.17)
LAND = (0.20, 0.25, 0.35)
BORDER = (0.36, 0.42, 0.55)
BAND = (0.95, 0.45, 0.18)
CENTRAL = (0.62, 0.16, 0.10)
PAPER = (1, 1, 1)

# ------------------------------------------------------------------ texter (fasta mallar, fyra språk)
MONTHS = {
    "sv": ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september", "oktober", "november", "december"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"],
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"],
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"],
}
DIRS = {
    "sv": ["norr", "nordost", "öster", "sydost", "söder", "sydväst", "väster", "nordväst"],
    "en": ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"],
    "de": ["Norden", "Nordosten", "Osten", "Südosten", "Süden", "Südwesten", "Westen", "Nordwesten"],
    "es": ["norte", "noreste", "este", "sureste", "sur", "suroeste", "oeste", "noroeste"],
    "fr": ["nord", "nord-est", "est", "sud-est", "sud", "sud-ouest", "ouest", "nord-ouest"],
}
# franska i löptext: riktningen med artikel ("vers le nord", "vers l’est")
DIRS_ART = {"fr": ["le nord", "le nord-est", "l’est", "le sud-est", "le sud", "le sud-ouest", "l’ouest", "le nord-ouest"]}
COMPASS = {"sv": "NÖSV", "en": "NESW", "de": "NOSW", "es": "NESO", "fr": "NESO"}


def dir_txt(lang, i):
    return DIRS_ART[lang][i] if lang in DIRS_ART else DIRS[lang][i]
T = {
    "sv": {
        "title": "Solförmörkelsen 2 augusti 2027",
        "sub": "Din personliga guide för {place}",
        "weekday": "måndag 2 augusti 2027",
        "c1": "Förmörkelsen börjar", "c2": "Totaliteten börjar", "max": "Maximum", "c3": "Totaliteten slutar", "c4": "Förmörkelsen slutar",
        "cover": "Täckning av solskivan", "mag": "Magnitud",
        "tot_yes": "Totalitet: JA – {dur}", "tot_no": "Totalitet: nej – förmörkelsen är partiell här",
        "dur": "{m} min {s} s",
        "nearest": "Närmaste plats med total förmörkelse ligger ca {km} km bort, åt {dir}.",
        "sunpos": "Vid maximum står solen {alt}° över horisonten i {dir} (azimut {az}°).",
        "tz": "Alla tider är lokal tid ({tz}, UTC{off}).",
        "timeline": "Förmörkelsens förlopp sett från {place}",
        "timeline_note": "Så täcker månen solen. Uppåt i bilden är uppåt mot himlen (zenit).",
        "sky": "Var på himlen solen står",
        "sky_note": "Sett uppifrån: mitten är rakt upp, kanten är horisonten.",
        "start": "Start", "end": "Slut",
        "what": "Det här kommer du att märka",
        "exp_low": "Solen ser ut som om någon tagit ett bett ur den. Utan skyddsglasögon märks det knappt – dagsljuset är nästan som vanligt.",
        "exp_mid": "Dagsljuset blir svagare och får en kallare ton. Skuggor blir skarpare. Genom lövverk syns små skärformade solbilder på marken.",
        "exp_high": "Det blir märkbart skumt och svalare, och ljuset får en märklig metallisk ton. Under träd syns hundratals små skärformade solbilder.",
        "exp_tot": "När månen täcker hela solen blir det skymningsmörkt mitt på dagen. De ljusaste planeterna och stjärnorna syns, solens korona lyser runt den svarta månen och temperaturen sjunker flera grader. Just före och efter syns ”diamantringen”.",
        "map": "Totalitetens bana",
        "map_band": "Total förmörkelse (månen täcker hela solen)",
        "map_you": "Din plats",
        "map_note": "Utanför bandet är förmörkelsen partiell. Ju närmare bandet, desto större del av solen täcks.",
        "safety": "Säkerhet – läs före förmörkelsen",
        "s1": "Titta aldrig direkt på solen utan godkända solförmörkelseglasögon enligt standarden ISO 12312-2. Vanliga solglasögon skyddar inte, inte ens flera par på varandra.",
        "s2": "Kontrollera glasögonen före användning: märkningen ISO 12312-2 och tillverkarens namn ska finnas, filtret får inte ha repor eller hål. Kasta skadade glasögon.",
        "s3": "Titta aldrig på solen genom kamera, kikare eller teleskop med glasögonen på – optiken koncentrerar ljuset och förstör både filter och öga. Optik kräver ett eget solfilter framför objektivet.",
        "s4": "Barn ska alltid ha en vuxen bredvid sig och ha glasögonen på hela tiden de tittar mot solen.",
        "s5_tot": "Bara under själva totaliteten, när solen är helt täckt, är det säkert att ta av glasögonen. Sätt på dem igen så fort den första ljuspunkten syns.",
        "s5_part": "Här blir förmörkelsen partiell. Då är det aldrig säkert att titta på solen utan skydd – inte ens när nästan hela solen är täckt.",
        "s6": "Ett säkert alternativ: gör ett hål med en nål i en kartongbit och låt solen lysa igenom mot ett vitt papper. Du ser den förmörkade solen som en liten projicerad bild.",
        "prep": "Så förbereder du dig",
        "p1": "Skaffa godkända glasögon i god tid – en per person.",
        "p2": "Välj en plats med fri sikt mot {dir} och gå ut minst 15 minuter före starten klockan {c1}.",
        "p3": "Titta på omgivningen också: ljuset, skuggorna, fåglarna och temperaturen förändras.",
        "p4": "Vill du fotografera solen krävs solfilter framför linsen. Utan filter kan du fotografera ljuset och skuggorna.",
        "weather": "Beräkningen gäller en klar himmel. Vädret kan ingen förutsäga ett år i förväg.",
        "credit": "Beräknat med Skyfield (MIT) och JPL:s efemerid DE421. Karta: Natural Earth (public domain). "
                  "Kontrollerat automatiskt mot oberoende beräkning med Besselska element från NASA – Eclipse Predictions by Fred Espenak, NASA's GSFC. "
                  "Typsnitt: Noto (SIL OFL 1.1). Moodly Sverige.",
        "poster_title": "Solförmörkelsen",
        "poster_date": "2 augusti 2027",
        "page": "Sida {n} av 3",
    },
    "en": {
        "title": "The Solar Eclipse of 2 August 2027",
        "sub": "Your personal guide for {place}",
        "weekday": "Monday, 2 August 2027",
        "c1": "Eclipse begins", "c2": "Totality begins", "max": "Maximum", "c3": "Totality ends", "c4": "Eclipse ends",
        "cover": "Share of the Sun covered", "mag": "Magnitude",
        "tot_yes": "Totality: YES – {dur}", "tot_no": "Totality: no – the eclipse is partial here",
        "dur": "{m} min {s} s",
        "nearest": "The nearest place with a total eclipse is about {km} km away, to the {dir}.",
        "sunpos": "At maximum the Sun is {alt}° above the horizon in the {dir} (azimuth {az}°).",
        "tz": "All times are local time ({tz}, UTC{off}).",
        "timeline": "How the eclipse unfolds, seen from {place}",
        "timeline_note": "How the Moon covers the Sun. Up in the picture is up in the sky (towards the zenith).",
        "sky": "Where the Sun is in the sky",
        "sky_note": "Seen from above: the centre is straight up, the edge is the horizon.",
        "start": "Start", "end": "End",
        "what": "What you will notice",
        "exp_low": "The Sun looks as if someone has taken a bite out of it. Without eclipse glasses you will hardly notice – daylight looks almost normal.",
        "exp_mid": "Daylight becomes weaker and cooler in tone. Shadows get sharper. Under trees you will see small crescent-shaped images of the Sun on the ground.",
        "exp_high": "It gets noticeably dim and cooler, and the light takes on a strange, metallic tone. Under trees you will see hundreds of small crescent Suns.",
        "exp_tot": "When the Moon covers the whole Sun, it becomes as dark as dusk in the middle of the day. The brightest planets and stars appear, the Sun's corona shines around the black Moon and the temperature drops by several degrees. Just before and after you can see the \"diamond ring\".",
        "map": "The path of totality",
        "map_band": "Total eclipse (the Moon covers the whole Sun)",
        "map_you": "Your location",
        "map_note": "Outside the band the eclipse is partial. The closer to the band, the more of the Sun is covered.",
        "safety": "Safety – read before the eclipse",
        "s1": "Never look directly at the Sun without certified eclipse glasses that meet the ISO 12312-2 standard. Ordinary sunglasses do not protect your eyes, not even several pairs on top of each other.",
        "s2": "Check your glasses before use: they must be marked ISO 12312-2 and show the manufacturer's name, and the filter must have no scratches or holes. Throw away damaged glasses.",
        "s3": "Never look at the Sun through a camera, binoculars or a telescope while wearing eclipse glasses – the optics concentrate the light and destroy both the filter and your eye. Optics need their own solar filter in front of the lens.",
        "s4": "Children should always have an adult beside them and keep their glasses on the whole time they look towards the Sun.",
        "s5_tot": "Only during totality itself, when the Sun is completely covered, is it safe to take the glasses off. Put them back on as soon as the first point of light appears.",
        "s5_part": "The eclipse is partial here. It is never safe to look at the Sun without protection – not even when almost all of it is covered.",
        "s6": "A safe alternative: make a hole with a pin in a piece of card and let the Sun shine through onto a white sheet of paper. You will see the eclipsed Sun as a small projected image.",
        "prep": "How to prepare",
        "p1": "Get certified glasses in good time – one pair per person.",
        "p2": "Choose a spot with a clear view to the {dir} and go outside at least 15 minutes before the start at {c1}.",
        "p3": "Look around you too: the light, the shadows, the birds and the temperature all change.",
        "p4": "To photograph the Sun you need a solar filter in front of the lens. Without a filter you can photograph the light and the shadows.",
        "weather": "The calculation assumes a clear sky. Nobody can forecast the weather a year ahead.",
        "credit": "Calculated with Skyfield (MIT) and the JPL ephemeris DE421. Map: Natural Earth (public domain). "
                  "Automatically checked against an independent calculation with Besselian elements from NASA – Eclipse Predictions by Fred Espenak, NASA's GSFC. "
                  "Fonts: Noto (SIL OFL 1.1). Moodly Sverige.",
        "poster_title": "The Solar Eclipse",
        "poster_date": "2 August 2027",
        "page": "Page {n} of 3",
    },
    "de": {
        "title": "Die Sonnenfinsternis am 2. August 2027",
        "sub": "Ihr persönlicher Leitfaden für {place}",
        "weekday": "Montag, 2. August 2027",
        "c1": "Beginn der Finsternis", "c2": "Beginn der Totalität", "max": "Maximum", "c3": "Ende der Totalität", "c4": "Ende der Finsternis",
        "cover": "Bedeckter Anteil der Sonne", "mag": "Größe",
        "tot_yes": "Totalität: JA – {dur}", "tot_no": "Totalität: nein – die Finsternis ist hier partiell",
        "dur": "{m} min {s} s",
        "nearest": "Der nächste Ort mit totaler Finsternis liegt etwa {km} km entfernt in Richtung {dir}.",
        "sunpos": "Beim Maximum steht die Sonne {alt}° über dem Horizont im {dir} (Azimut {az}°).",
        "tz": "Alle Zeiten in Ortszeit ({tz}, UTC{off}).",
        "timeline": "Der Verlauf der Finsternis von {place} aus gesehen",
        "timeline_note": "So bedeckt der Mond die Sonne. Oben im Bild ist oben am Himmel (Zenit).",
        "sky": "Wo die Sonne am Himmel steht",
        "sky_note": "Von oben gesehen: die Mitte ist senkrecht über Ihnen, der Rand ist der Horizont.",
        "start": "Beginn", "end": "Ende",
        "what": "Was Sie bemerken werden",
        "exp_low": "Die Sonne sieht aus, als hätte jemand ein Stück abgebissen. Ohne Finsternisbrille merkt man kaum etwas – das Tageslicht wirkt fast normal.",
        "exp_mid": "Das Tageslicht wird schwächer und kühler. Schatten werden schärfer. Unter Bäumen sieht man kleine sichelförmige Sonnenbilder auf dem Boden.",
        "exp_high": "Es wird merklich dämmrig und kühler, das Licht bekommt einen seltsamen, metallischen Ton. Unter Bäumen erscheinen Hunderte kleiner Sonnensicheln.",
        "exp_tot": "Wenn der Mond die ganze Sonne bedeckt, wird es mitten am Tag dämmrig dunkel. Die hellsten Planeten und Sterne erscheinen, die Korona der Sonne leuchtet um den schwarzen Mond, und die Temperatur sinkt um mehrere Grad. Kurz davor und danach sieht man den „Diamantring“.",
        "map": "Der Pfad der Totalität",
        "map_band": "Totale Finsternis (der Mond bedeckt die ganze Sonne)",
        "map_you": "Ihr Ort",
        "map_note": "Außerhalb des Bandes ist die Finsternis partiell. Je näher am Band, desto mehr von der Sonne wird bedeckt.",
        "safety": "Sicherheit – vor der Finsternis lesen",
        "s1": "Schauen Sie niemals direkt in die Sonne ohne zertifizierte Finsternisbrille nach der Norm ISO 12312-2. Normale Sonnenbrillen schützen nicht, auch nicht mehrere übereinander.",
        "s2": "Prüfen Sie die Brille vor dem Gebrauch: Sie muss die Kennzeichnung ISO 12312-2 und den Namen des Herstellers tragen, und der Filter darf keine Kratzer oder Löcher haben. Beschädigte Brillen wegwerfen.",
        "s3": "Schauen Sie niemals mit Finsternisbrille durch Kamera, Fernglas oder Teleskop – die Optik bündelt das Licht und zerstört Filter und Auge. Optik braucht einen eigenen Sonnenfilter vor dem Objektiv.",
        "s4": "Kinder sollten immer einen Erwachsenen neben sich haben und die Brille die ganze Zeit tragen, während sie zur Sonne schauen.",
        "s5_tot": "Nur während der Totalität selbst, wenn die Sonne vollständig bedeckt ist, darf man die Brille abnehmen. Setzen Sie sie wieder auf, sobald der erste Lichtpunkt erscheint.",
        "s5_part": "Die Finsternis ist hier partiell. Es ist nie sicher, ohne Schutz in die Sonne zu schauen – auch nicht, wenn fast die ganze Sonne bedeckt ist.",
        "s6": "Eine sichere Alternative: Stechen Sie mit einer Nadel ein Loch in ein Stück Karton und lassen Sie die Sonne auf ein weißes Blatt scheinen. Sie sehen die verfinsterte Sonne als kleines projiziertes Bild.",
        "prep": "So bereiten Sie sich vor",
        "p1": "Besorgen Sie rechtzeitig zertifizierte Brillen – eine pro Person.",
        "p2": "Wählen Sie einen Platz mit freier Sicht nach {dir} und gehen Sie mindestens 15 Minuten vor Beginn um {c1} nach draußen.",
        "p3": "Achten Sie auch auf die Umgebung: Licht, Schatten, Vögel und Temperatur verändern sich.",
        "p4": "Um die Sonne zu fotografieren, brauchen Sie einen Sonnenfilter vor dem Objektiv. Ohne Filter können Sie Licht und Schatten fotografieren.",
        "weather": "Die Berechnung gilt für einen klaren Himmel. Das Wetter kann niemand ein Jahr im Voraus vorhersagen.",
        "credit": "Berechnet mit Skyfield (MIT) und der JPL-Ephemeride DE421. Karte: Natural Earth (gemeinfrei). "
                  "Automatisch geprüft gegen eine unabhängige Berechnung mit Besselschen Elementen der NASA – Eclipse Predictions by Fred Espenak, NASA's GSFC. "
                  "Schriften: Noto (SIL OFL 1.1). Moodly Sverige.",
        "poster_title": "Die Sonnenfinsternis",
        "poster_date": "2. August 2027",
        "page": "Seite {n} von 3",
    },
    "es": {
        "title": "El eclipse solar del 2 de agosto de 2027",
        "sub": "Tu guía personal para {place}",
        "weekday": "lunes, 2 de agosto de 2027",
        "c1": "Comienza el eclipse", "c2": "Comienza la totalidad", "max": "Máximo", "c3": "Termina la totalidad", "c4": "Termina el eclipse",
        "cover": "Parte del Sol cubierta", "mag": "Magnitud",
        "tot_yes": "Totalidad: SÍ – {dur}", "tot_no": "Totalidad: no – aquí el eclipse es parcial",
        "dur": "{m} min {s} s",
        "nearest": "El lugar más cercano con eclipse total está a unos {km} km, hacia el {dir}.",
        "sunpos": "En el máximo, el Sol está a {alt}° sobre el horizonte, hacia el {dir} (acimut {az}°).",
        "tz": "Todas las horas son locales ({tz}, UTC{off}).",
        "timeline": "Cómo avanza el eclipse visto desde {place}",
        "timeline_note": "Así cubre la Luna al Sol. Arriba en la imagen es arriba en el cielo (hacia el cenit).",
        "sky": "Dónde está el Sol en el cielo",
        "sky_note": "Visto desde arriba: el centro es justo encima de ti, el borde es el horizonte.",
        "start": "Inicio", "end": "Fin",
        "what": "Lo que notarás",
        "exp_low": "El Sol parece como si alguien le hubiera dado un mordisco. Sin gafas de eclipse apenas se nota: la luz del día parece casi normal.",
        "exp_mid": "La luz del día se debilita y se vuelve más fría. Las sombras se hacen más nítidas. Bajo los árboles verás pequeñas imágenes del Sol en forma de media luna.",
        "exp_high": "Se nota claramente más oscuro y fresco, y la luz adquiere un extraño tono metálico. Bajo los árboles aparecen cientos de pequeños soles en forma de media luna.",
        "exp_tot": "Cuando la Luna cubre todo el Sol, se hace tan oscuro como al anochecer en pleno día. Aparecen los planetas y las estrellas más brillantes, la corona solar brilla alrededor de la Luna negra y la temperatura baja varios grados. Justo antes y después se ve el «anillo de diamante».",
        "map": "La franja de totalidad",
        "map_band": "Eclipse total (la Luna cubre todo el Sol)",
        "map_you": "Tu ubicación",
        "map_note": "Fuera de la franja el eclipse es parcial. Cuanto más cerca de la franja, mayor parte del Sol queda cubierta.",
        "safety": "Seguridad – léelo antes del eclipse",
        "s1": "Nunca mires directamente al Sol sin gafas de eclipse certificadas según la norma ISO 12312-2. Las gafas de sol normales no protegen, ni siquiera varias puestas una sobre otra.",
        "s2": "Revisa las gafas antes de usarlas: deben llevar la marca ISO 12312-2 y el nombre del fabricante, y el filtro no puede tener rayas ni agujeros. Tira las gafas dañadas.",
        "s3": "Nunca mires al Sol con las gafas puestas a través de una cámara, unos prismáticos o un telescopio: la óptica concentra la luz y destruye el filtro y el ojo. La óptica necesita su propio filtro solar delante del objetivo.",
        "s4": "Los niños deben tener siempre a un adulto al lado y llevar las gafas puestas todo el tiempo que miren hacia el Sol.",
        "s5_tot": "Solo durante la totalidad, cuando el Sol está completamente cubierto, es seguro quitarse las gafas. Vuelve a ponértelas en cuanto aparezca el primer punto de luz.",
        "s5_part": "Aquí el eclipse es parcial. Nunca es seguro mirar al Sol sin protección, ni siquiera cuando casi todo está cubierto.",
        "s6": "Una alternativa segura: haz un agujero con un alfiler en un trozo de cartón y deja que el Sol brille a través de él sobre un papel blanco. Verás el Sol eclipsado como una pequeña imagen proyectada.",
        "prep": "Cómo prepararte",
        "p1": "Consigue gafas certificadas con tiempo: unas por persona.",
        "p2": "Elige un lugar con vista despejada hacia el {dir} y sal al menos 15 minutos antes del inicio, a las {c1}.",
        "p3": "Mira también a tu alrededor: la luz, las sombras, los pájaros y la temperatura cambian.",
        "p4": "Para fotografiar el Sol necesitas un filtro solar delante del objetivo. Sin filtro puedes fotografiar la luz y las sombras.",
        "weather": "El cálculo supone un cielo despejado. Nadie puede prever el tiempo con un año de antelación.",
        "credit": "Calculado con Skyfield (MIT) y la efeméride DE421 del JPL. Mapa: Natural Earth (dominio público). "
                  "Comprobado automáticamente con un cálculo independiente a partir de los elementos besselianos de la NASA – Eclipse Predictions by Fred Espenak, NASA's GSFC. "
                  "Tipografía: Noto (SIL OFL 1.1). Moodly Sverige.",
        "poster_title": "El eclipse solar",
        "poster_date": "2 de agosto de 2027",
        "page": "Página {n} de 3",
    },
    "fr": {
        "title": "L’éclipse solaire du 2 août 2027",
        "sub": "Votre guide personnel pour {place}",
        "weekday": "lundi 2 août 2027",
        "c1": "Début de l’éclipse", "c2": "Début de la totalité", "max": "Maximum", "c3": "Fin de la totalité", "c4": "Fin de l’éclipse",
        "cover": "Part du Soleil couverte", "mag": "Magnitude",
        "tot_yes": "Totalité : OUI – {dur}", "tot_no": "Totalité : non – l’éclipse est partielle ici",
        "dur": "{m} min {s} s",
        "nearest": "Le lieu le plus proche avec une éclipse totale se trouve à environ {km} km, vers {dir}.",
        "sunpos": "Au maximum, le Soleil est à {alt}° au-dessus de l’horizon, vers {dir} (azimut {az}°).",
        "tz": "Toutes les heures sont en heure locale ({tz}, UTC{off}).",
        "timeline": "Le déroulement de l’éclipse vu depuis {place}",
        "timeline_note": "Ainsi la Lune couvre le Soleil. Le haut de l’image est le haut du ciel (vers le zénith).",
        "sky": "Où se trouve le Soleil dans le ciel",
        "sky_note": "Vu d’en haut : le centre est juste au-dessus de vous, le bord est l’horizon.",
        "start": "Début", "end": "Fin",
        "what": "Ce que vous allez remarquer",
        "exp_low": "Le Soleil semble avoir été croqué. Sans lunettes d’éclipse, on le remarque à peine : la lumière du jour paraît presque normale.",
        "exp_mid": "La lumière du jour faiblit et prend un ton plus froid. Les ombres deviennent plus nettes. Sous les arbres, de petites images du Soleil en forme de croissant apparaissent au sol.",
        "exp_high": "Il fait nettement plus sombre et plus frais, et la lumière prend un étrange ton métallique. Sous les arbres apparaissent des centaines de petits croissants de Soleil.",
        "exp_tot": "Quand la Lune couvre tout le Soleil, il fait aussi sombre qu’au crépuscule en plein jour. Les planètes et les étoiles les plus brillantes apparaissent, la couronne solaire brille autour de la Lune noire et la température baisse de plusieurs degrés. Juste avant et juste après, on voit « l’anneau de diamant ».",
        "map": "La bande de totalité",
        "map_band": "Éclipse totale (la Lune couvre tout le Soleil)",
        "map_you": "Votre lieu",
        "map_note": "En dehors de la bande, l’éclipse est partielle. Plus on est proche de la bande, plus la part du Soleil couverte est grande.",
        "safety": "Sécurité – à lire avant l’éclipse",
        "s1": "Ne regardez jamais directement le Soleil sans lunettes d’éclipse certifiées selon la norme ISO 12312-2. Les lunettes de soleil ordinaires ne protègent pas, même plusieurs paires superposées.",
        "s2": "Vérifiez vos lunettes avant de les utiliser : elles doivent porter la mention ISO 12312-2 et le nom du fabricant, et le filtre ne doit avoir ni rayure ni trou. Jetez les lunettes abîmées.",
        "s3": "Ne regardez jamais le Soleil à travers un appareil photo, des jumelles ou un télescope avec des lunettes d’éclipse : l’optique concentre la lumière et détruit le filtre et l’œil. Une optique a besoin de son propre filtre solaire devant l’objectif.",
        "s4": "Les enfants doivent toujours avoir un adulte à côté d’eux et garder leurs lunettes tout le temps qu’ils regardent vers le Soleil.",
        "s5_tot": "Ce n’est que pendant la totalité, quand le Soleil est entièrement couvert, que l’on peut retirer ses lunettes. Remettez-les dès que le premier point de lumière apparaît.",
        "s5_part": "Ici, l’éclipse est partielle. Il n’est jamais sûr de regarder le Soleil sans protection, même quand il est presque entièrement couvert.",
        "s6": "Une alternative sûre : percez un trou avec une épingle dans un morceau de carton et laissez le Soleil briller à travers sur une feuille blanche. Vous verrez le Soleil éclipsé sous la forme d’une petite image projetée.",
        "prep": "Comment vous préparer",
        "p1": "Procurez-vous à temps des lunettes certifiées, une paire par personne.",
        "p2": "Choisissez un endroit avec une vue dégagée vers {dir} et sortez au moins 15 minutes avant le début, à {c1}.",
        "p3": "Regardez aussi autour de vous : la lumière, les ombres, les oiseaux et la température changent.",
        "p4": "Pour photographier le Soleil, il faut un filtre solaire devant l’objectif. Sans filtre, vous pouvez photographier la lumière et les ombres.",
        "weather": "Le calcul suppose un ciel dégagé. Personne ne peut prévoir la météo un an à l’avance.",
        "credit": "Calculé avec Skyfield (MIT) et l’éphéméride DE421 du JPL. Carte : Natural Earth (domaine public). "
                  "Vérifié automatiquement par un calcul indépendant à partir des éléments besséliens de la NASA – Eclipse Predictions by Fred Espenak, NASA's GSFC. "
                  "Polices : Noto (SIL OFL 1.1). Moodly Sverige.",
        "poster_title": "L’éclipse solaire",
        "poster_date": "2 août 2027",
        "page": "Page {n} sur 3",
    },
}
FOREIGN = {"fr": ["Förmörkelsen", "Finsternis", "eclipse begins", "Totalidad"],
           "sv": ["Finsternis", "eclipse begins", "Totalidad"], "en": ["Förmörkelsen", "Finsternis", "Totalidad"],
           "de": ["Förmörkelsen", "eclipse begins", "Totalidad"], "es": ["Förmörkelsen", "Finsternis", "eclipse begins"]}


def compass8(az):
    return int(((az % 360) + 22.5) // 45) % 8


# ------------------------------------------------------------------ beräkning (Skyfield)
class Sky:
    def __init__(self):
        load = Loader(str(DATA), verbose=False)
        self.ts = load.timescale(builtin=True)
        self.eph = load("de421.bsp")
        self.sun, self.moon, self.earth = self.eph["sun"], self.eph["moon"], self.eph["earth"]

    def geom(self, obs, t):
        s = obs.at(t).observe(self.sun).apparent()
        m = obs.at(t).observe(self.moon).apparent()
        sep = s.separation_from(m).radians
        rs = np.arcsin(R_SUN_KM / s.distance().km)
        rmp = np.arcsin(K_PEN * R_EQ_KM / m.distance().km)
        rmu = np.arcsin(K_UMB * R_EQ_KM / m.distance().km)
        salt, saz, _ = s.altaz()
        malt, maz, _ = m.altaz()
        return dict(sep=sep, rs=rs, rmp=rmp, rmu=rmu, alt=salt.degrees, az=saz.degrees,
                    malt=malt.degrees, maz=maz.degrees)


def overlap(rs, rm, d):
    if d >= rs + rm:
        return 0.0
    if d <= abs(rm - rs):
        return 1.0 if rm >= rs else (rm / rs) ** 2
    a1 = math.acos((d * d + rs * rs - rm * rm) / (2 * d * rs))
    a2 = math.acos((d * d + rm * rm - rs * rs) / (2 * d * rm))
    return (rs * rs * (a1 - math.sin(2 * a1) / 2) + rm * rm * (a2 - math.sin(2 * a2) / 2)) / (math.pi * rs * rs)


def local_circumstances(sky, lat, lon, elev=0.0, day=DAY):
    ts = sky.ts
    obs = sky.earth + wgs84.latlon(lat, lon, elevation_m=elev)
    jd0 = ts.utc(*day).tt
    grid = ts.tt_jd(jd0 + np.arange(0, 1.0001, 1 / 720))  # varannan minut hela dygnet (UTC-dygnet)
    g = sky.geom(obs, grid)
    pen = g["sep"] - (g["rs"] + g["rmp"])
    i = int(np.argmin(g["sep"] - g["rs"] - g["rmp"]))
    res = {"lat": lat, "lon": lon, "elevation_m": elev}
    if pen[i] >= 0:
        res["type"] = "none"; res["obscuration"] = 0.0; return res
    # optimera i sekunder relativt rutnätspunkten (annars blir toleransen relativ till JD ≈ 2,46 miljoner)
    base = float(grid.tt[i])
    f_sep = lambda s_: float(sky.geom(obs, ts.tt_jd(base + s_ / 86400))["sep"])
    tm = base + minimize_scalar(f_sep, bounds=(-240, 240), method="bounded", options={"xatol": 0.01}).x / 86400

    def gpen(jd):
        q = sky.geom(obs, ts.tt_jd(jd)); return float(q["sep"] - q["rs"] - q["rmp"])

    def gumb(jd):
        q = sky.geom(obs, ts.tt_jd(jd)); return float(q["sep"] - abs(q["rmu"] - q["rs"]))

    j1 = max(k for k in range(0, i + 1) if pen[k] >= 0) if (pen[:i + 1] >= 0).any() else 0
    j4 = min(k for k in range(i, len(pen)) if pen[k] >= 0)
    c1 = brentq(gpen, grid.tt[j1], tm, xtol=1e-9)
    c4 = brentq(gpen, tm, grid.tt[j4], xtol=1e-9)
    gm = sky.geom(obs, ts.tt_jd(tm))
    rs, rmp, rmu, sep = float(gm["rs"]), float(gm["rmp"]), float(gm["rmu"]), float(gm["sep"])
    contacts = {"c1": c1, "max": tm, "c4": c4}
    if sep < abs(rmu - rs):
        contacts["c2"] = brentq(gumb, tm - 0.01, tm, xtol=1e-9)
        contacts["c3"] = brentq(gumb, tm, tm + 0.01, xtol=1e-9)
        res["type"] = "total" if rmu > rs else "annular"
        res["duration_s"] = (contacts["c3"] - contacts["c2"]) * 86400
        res["obscuration"] = 1.0 if rmu > rs else (rmu / rs) ** 2
        res["magnitude"] = rmu / rs
    else:
        res["type"] = "partial"
        res["obscuration"] = overlap(rs, rmp, sep)
        res["magnitude"] = (rs + rmp - sep) / (2 * rs)
    res["contacts_utc"] = {}
    res["sun_altaz"] = {}
    for k, jd in contacts.items():
        t = ts.tt_jd(jd)
        res["contacts_utc"][k] = t.utc_datetime().isoformat()
        q = sky.geom(obs, t)
        res["sun_altaz"][k] = [float(q["alt"]), float(q["az"])]
    return res


def timeline(sky, lat, lon, circ, n=7, elev=0.0):
    """n ögonblicksbilder från C1 till C4 i observatörens alt-az-plan (x åt höger = ökande azimut, y uppåt = zenit)."""
    ts = sky.ts
    obs = sky.earth + wgs84.latlon(lat, lon, elevation_m=elev)
    c = circ["contacts_utc"]
    t1, tm, t4 = (ts.from_datetime(datetime.fromisoformat(c[k])).tt for k in ("c1", "max", "c4"))
    half = n // 2
    jds = [t1 + (tm - t1) * i / half for i in range(half)] + [tm] + [tm + (t4 - tm) * i / half for i in range(1, half + 1)]
    out = []
    for jd in jds:
        q = sky.geom(obs, ts.tt_jd(jd))
        dx = ((q["maz"] - q["az"] + 180) % 360 - 180) * math.cos(math.radians(q["alt"]))
        dy = q["malt"] - q["alt"]
        rs = math.degrees(q["rs"])
        out.append({"utc": ts.tt_jd(jd).utc_datetime().isoformat(), "dx_rs": dx / rs, "dy_rs": dy / rs,
                    "rm_rs": float(q["rmp"] / q["rs"]), "alt": float(q["alt"])})
    return out


def _wgs84_xyz(lat, lon):
    e2 = FLAT * (2 - FLAT)
    la, lo = np.radians(lat), np.radians(lon)
    N = R_EQ_KM / np.sqrt(1 - e2 * np.sin(la) ** 2)
    return np.stack([N * np.cos(la) * np.cos(lo), N * np.cos(la) * np.sin(lo), N * (1 - e2) * np.sin(la)], -1)


def totality_band(sky):
    """Totalitetsbandet per longitud (0,5°): södra gräns, centrallinje, norra gräns. Cachas på disk."""
    cache = DATA / "cache" / f"bana_{GENERATOR_VERSION.replace('/', '_')}.json"
    if cache.exists():
        return json.load(open(cache, encoding="utf-8"))
    ts = sky.ts
    t = ts.utc(2027, 8, 2, 8, 0, np.arange(0, 4 * 3600, 10))
    e = sky.earth.at(t)
    S = e.observe(sky.sun).apparent().frame_xyz(itrs).km.T   # (N,3) geocentrisk, jordfast
    M = e.observe(sky.moon).apparent().frame_xyz(itrs).km.T
    # centrallinje per tidpunkt: skuggaxelns skärning med ellipsoiden
    u = (M - S) / np.linalg.norm(M - S, axis=1)[:, None]
    k = 1 / (1 - FLAT)
    Ms, us = M * [1, 1, k], u * [1, 1, k]
    a, b, c = (us * us).sum(1), 2 * (Ms * us).sum(1), (Ms * Ms).sum(1) - R_EQ_KM ** 2
    disc = b * b - 4 * a * c
    hit = disc > 0
    s_ = (-b - np.sqrt(np.where(hit, disc, 0))) / (2 * a)
    P = M + s_[:, None] * u
    plon = np.degrees(np.arctan2(P[:, 1], P[:, 0]))
    plat = np.degrees(np.arctan2(P[:, 2], (1 - FLAT) ** 2 * np.hypot(P[:, 0], P[:, 1])))
    idx = np.where(hit)[0]
    band = []
    for lon in np.arange(-45.0, 95.01, 0.5):
        # tider då centrallinjen passerar denna longitud
        d = np.abs(plon[idx] - lon)
        j = idx[np.argmin(d)]
        if d.min() > 1.0:
            continue
        clat = plat[j]
        tsel = np.arange(max(0, j - 60), min(len(t.tt), j + 61))
        lats = np.arange(clat - 3.0, clat + 3.0001, 0.02)
        O = _wgs84_xyz(lats, np.full_like(lats, lon))                  # (L,3)
        up = O / np.linalg.norm(O, axis=1)[:, None]
        vs = S[tsel][None, :, :] - O[:, None, :]                       # (L,T,3)
        vm = M[tsel][None, :, :] - O[:, None, :]
        ds, dm = np.linalg.norm(vs, axis=2), np.linalg.norm(vm, axis=2)
        sep = np.arccos(np.clip((vs * vm).sum(2) / (ds * dm), -1, 1))
        g = sep - (np.arcsin(K_UMB * R_EQ_KM / dm) - np.arcsin(R_SUN_KM / ds))
        sun_up = (vs * up[:, None, :]).sum(2) > 0
        g = np.where(sun_up, g, 1.0).min(1)                           # (L,)
        inside = g < 0
        if not inside.any():
            continue
        ii = np.where(inside)[0]
        i0, i1 = ii[0], ii[-1]
        if i0 == 0 or i1 == len(lats) - 1:
            continue  # bandet når fönsterkanten -> osäkert, hoppa över kolumnen
        s_lat = lats[i0 - 1] + (lats[i0] - lats[i0 - 1]) * g[i0 - 1] / (g[i0 - 1] - g[i0])
        n_lat = lats[i1] + (lats[i1 + 1] - lats[i1]) * g[i1] / (g[i1] - g[i1 + 1])
        cm = int(np.argmin(g))
        band.append([round(float(lon), 2), round(float(s_lat), 4), round(float(lats[cm]), 4), round(float(n_lat), 4)])
    cache.parent.mkdir(exist_ok=True)
    json.dump({"generator": GENERATOR_VERSION, "format": "[lon, sydgräns, central, nordgräns]", "band": band},
              open(cache, "w", encoding="utf-8"))
    return json.load(open(cache, encoding="utf-8"))


def haversine(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(a))


def nearest_totality(band, lat, lon):
    b = np.array(band["band"])
    # förtäta gränslinjerna
    lons = np.arange(b[0, 0], b[-1, 0], 0.05)
    sl, nl = np.interp(lons, b[:, 0], b[:, 1]), np.interp(lons, b[:, 0], b[:, 3])
    lat_all = np.concatenate([sl, nl]); lon_all = np.concatenate([lons, lons])
    d = haversine(lat, lon, lat_all, lon_all)
    j = int(np.argmin(d))
    p1, p2 = math.radians(lat), math.radians(lat_all[j])
    dl = math.radians(lon_all[j] - lon)
    brg = math.degrees(math.atan2(math.sin(dl) * math.cos(p2), math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl))) % 360
    return float(d[j]), brg


# ------------------------------------------------------------------ ritning
def wrap(text, font, size, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if pdfmetrics.stringWidth(test, font, size) <= width:
            cur = test
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    return lines


def para(c, text, x, y, width, font="Sans", size=10, lead=None, color=INK):
    lead = lead or size * 1.38
    c.setFont(font, size); c.setFillColorRGB(*color)
    for ln in wrap(text, font, size, width):
        c.drawString(x, y, ln); y -= lead
    return y


def fit_font(text, font, size, width, minsize=9):
    while size > minsize and pdfmetrics.stringWidth(text, font, size) > width:
        size -= 0.5
    return size


def draw_disk(c, x, y, r, snap, bg=BG, total=False):
    """Sol med månskiva. snap: dx_rs, dy_rs, rm_rs (i solradier)."""
    if total:
        c.saveState()
        for i in range(24):  # mjuk korona: många tunna, halvgenomskinliga ringar
            c.setFillColorRGB(1, 0.97, 0.9, alpha=0.045)
            c.circle(x, y, r * (1.75 - 0.031 * i), stroke=0, fill=1)
        c.restoreState()
    c.setFillColorRGB(*SUN); c.circle(x, y, r, stroke=0, fill=1)
    c.saveState()
    p = c.beginPath(); p.circle(x, y, r); c.clipPath(p, stroke=0, fill=0)
    c.setFillColorRGB(*bg)
    c.circle(x + snap["dx_rs"] * r, y + snap["dy_rs"] * r, snap["rm_rs"] * r, stroke=0, fill=1)
    c.restoreState()
    if total:
        c.setFillColorRGB(*bg); c.circle(x, y, r * 1.0, stroke=0, fill=1)


def load_countries():
    return json.load(gzip.open(DATA / "ne_50m_lander_utdrag.json.gz", "rt", encoding="utf-8"))["features"]


def draw_map(c, box, extent, band, place_ll, lang, fel=""):
    x0, y0, w, h = box
    lon0, lon1, lat0, lat1 = extent
    P = lambda lo, la: (x0 + (lo - lon0) / (lon1 - lon0) * w, y0 + (la - lat0) / (lat1 - lat0) * h)
    c.setFillColorRGB(*SEA); c.rect(x0, y0, w, h, stroke=0, fill=1)
    c.saveState()
    p = c.beginPath(); p.rect(x0, y0, w, h); c.clipPath(p, stroke=0, fill=0)
    c.setFillColorRGB(*LAND); c.setStrokeColorRGB(*BORDER); c.setLineWidth(0.3)
    for f in load_countries():
        for ring in f["rings"]:
            xs = [q[0] for q in ring]; ys = [q[1] for q in ring]
            if max(xs) < lon0 - 1 or min(xs) > lon1 + 1 or max(ys) < lat0 - 1 or min(ys) > lat1 + 1:
                continue
            pa = c.beginPath()
            X, Y = P(*ring[0]); pa.moveTo(X, Y)
            for q in ring[1:]:
                X, Y = P(*q); pa.lineTo(X, Y)
            pa.close(); c.drawPath(pa, stroke=1, fill=1)
    b = band["band"]
    shift = 1.5 if fel == "bana_forskjuten" else 0.0
    pa = c.beginPath()
    X, Y = P(b[0][0], b[0][3] + shift); pa.moveTo(X, Y)
    for q in b[1:]:
        X, Y = P(q[0], q[3] + shift); pa.lineTo(X, Y)
    for q in reversed(b):
        X, Y = P(q[0], q[1] + shift); pa.lineTo(X, Y)
    pa.close()
    c.setFillColorRGB(*BAND, alpha=0.85); c.drawPath(pa, stroke=0, fill=1)
    c.setStrokeColorRGB(*CENTRAL); c.setLineWidth(0.6)
    pa = c.beginPath(); X, Y = P(b[0][0], b[0][2] + shift); pa.moveTo(X, Y)
    for q in b[1:]:
        X, Y = P(q[0], q[2] + shift); pa.lineTo(X, Y)
    c.drawPath(pa, stroke=1, fill=0)
    # gradnät
    c.setStrokeColorRGB(0.5, 0.55, 0.65, alpha=0.35); c.setLineWidth(0.25)
    for lo in range(int(math.ceil(lon0 / 10)) * 10, int(lon1) + 1, 10):
        X, _ = P(lo, 0); c.line(X, y0, X, y0 + h)
    for la in range(int(math.ceil(lat0 / 10)) * 10, int(lat1) + 1, 10):
        _, Y = P(0, la); c.line(x0, Y, x0 + w, Y)
    c.restoreState()
    # din plats
    mlat, mlon = place_ll
    if fel == "markor_fel":
        mlat += 3
    X, Y = P(mlon, mlat)
    c.setFillColorRGB(1, 1, 1); c.setStrokeColorRGB(0, 0, 0); c.setLineWidth(0.8)
    c.circle(X, Y, 2.2 * mm, stroke=1, fill=1)
    c.setFillColorRGB(0.1, 0.1, 0.1); c.circle(X, Y, 0.7 * mm, stroke=0, fill=1)
    c.setStrokeColorRGB(*GOLD); c.setLineWidth(0.6); c.rect(x0, y0, w, h, stroke=1, fill=0)
    return {"box_pt": [x0, y0, w, h], "extent": [lon0, lon1, lat0, lat1], "marker_pt": [X, Y]}


def sky_dome(c, cx, cy, R, circ, lang):
    c.setFillColorRGB(0.07, 0.10, 0.19); c.setStrokeColorRGB(*GOLD); c.setLineWidth(0.6)
    c.circle(cx, cy, R, stroke=1, fill=1)
    c.setStrokeColorRGB(0.45, 0.5, 0.62); c.setLineWidth(0.3)
    for alt in (30, 60):
        c.circle(cx, cy, R * (90 - alt) / 90, stroke=1, fill=0)
    c.setFont("Sans", 8); c.setFillColorRGB(*GOLD)
    for i, ch in enumerate(COMPASS[lang]):
        a = math.radians(i * 90)
        c.drawCentredString(cx + math.sin(a) * (R + 4 * mm), cy + math.cos(a) * (R + 4 * mm) - 3, ch)
    pts = {}
    for k in ("c1", "max", "c4"):
        alt, az = circ["sun_altaz"][k]
        r = R * (90 - max(alt, 0)) / 90
        pts[k] = (cx + r * math.sin(math.radians(az)), cy + r * math.cos(math.radians(az)))
    c.setStrokeColorRGB(*SUN); c.setLineWidth(0.8)
    c.line(*pts["c1"], *pts["max"]); c.line(*pts["max"], *pts["c4"])
    for n_, k in enumerate(("c1", "max", "c4"), 1):
        c.setFillColorRGB(*SUN); c.circle(*pts[k], 1.6 * mm if k == "max" else 1.0 * mm, stroke=0, fill=1)
        # siffra utåt från mitten så att den inte krockar med punkten
        vx, vy = pts[k][0] - cx, pts[k][1] - cy
        L = math.hypot(vx, vy) or 1.0
        ox, oy = (vx / L, vy / L) if L > 1 else (1, 0)
        off = 4.2 * mm + (1.5 * mm if n_ == 2 else 0)
        c.setFillColorRGB(*INK); c.setFont("Sans", 8)
        c.drawCentredString(pts[k][0] + ox * off + (-oy) * (n_ - 2) * 2.2 * mm, pts[k][1] + oy * off + ox * (n_ - 2) * 2.2 * mm - 2.5, str(n_))
    return {"center_pt": [cx, cy], "R_pt": R, "max_pt": list(pts["max"])}


def fmt_time(iso, tz, secs=True):
    d = datetime.fromisoformat(iso).astimezone(ZoneInfo(tz))
    if secs:
        return d.strftime("%H:%M:%S")
    d = (d + timedelta(seconds=30)).replace(second=0, microsecond=0)
    return d.strftime("%H:%M")


def render(order, circ, snaps, band, lang, path):
    tx = T[lang]
    tz = order["timezone"]
    total = circ["type"] == "total"
    loc_max = datetime.fromisoformat(circ["contacts_utc"]["max"]).astimezone(ZoneInfo(tz))
    off = loc_max.utcoffset().total_seconds() / 3600
    offs = f"{'+' if off >= 0 else '−'}{abs(off):g}"
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1, initialFontName="Sans", initialFontSize=10)
    c.setTitle(f"{tx['title']} – {order['place']}"); c.setAuthor(GENERATOR_VERSION)
    W, H = A4
    M = 18 * mm
    keys = ["c1"] + (["c2"] if total else []) + ["max"] + (["c3"] if total else []) + ["c4"]
    shown = {}
    for k in keys:
        iso = circ["contacts_utc"][k]
        if FEL == "tid_c1_plus2min" and k == "c1":
            iso = (datetime.fromisoformat(iso) + timedelta(minutes=2)).isoformat()
        shown[k] = iso
    pct = circ["obscuration"] * 100 + (3 if FEL == "tackning_plus3" else 0)
    pct_s = f"{min(pct, 100):.0f}" if total else f"{pct:.1f}".replace(".", "," if lang != "en" else ".")
    geo = {"pages": []}

    def page_bg():
        c.setFillColorRGB(*BG); c.rect(0, 0, c._pagesize[0], c._pagesize[1], stroke=0, fill=1)

    def footer(n):
        c.setFont("Sans", 6.3); c.setFillColorRGB(*MUTE)
        yy = 14 * mm
        for ln in wrap(tx["credit"], "Sans", 6.3, W - 2 * M):
            c.drawString(M, yy, ln); yy -= 8
        c.drawRightString(W - M, 20 * mm, tx["page"].format(n=n))

    # ---------------- sida 1: nyckeltal + tidslinje
    page_bg()
    c.setFillColorRGB(*GOLD); c.setFont("Sans", 9); c.drawString(M, H - 20 * mm, tx["weekday"].upper())
    tsize = fit_font(tx["title"], "Serif", 24, W - 2 * M)
    c.setFillColorRGB(*INK); c.setFont("Serif", tsize); c.drawString(M, H - 31 * mm, tx["title"])
    sub = tx["sub"].format(place=order["place"])
    c.setFont("SerifIt", fit_font(sub, "SerifIt", 14, W - 2 * M)); c.drawString(M, H - 40 * mm, sub)
    y = H - 47 * mm
    if order.get("text"):
        c.setFont("Sans", fit_font(order["text"], "Sans", 11, W - 2 * M)); c.setFillColorRGB(*GOLD)
        c.drawString(M, y, order["text"]); y -= 6 * mm
    # stor siffra
    y -= 16 * mm
    c.setFillColorRGB(*SUN); c.setFont("Serif", 40); c.drawString(M, y, f"{pct_s} %")
    c.setFillColorRGB(*INK); c.setFont("Sans", 10); c.drawString(M, y - 6 * mm, tx["cover"])
    lines_right = [tx["tot_yes"].format(dur=tx["dur"].format(m=int(circ["duration_s"] // 60), s=int(round(circ["duration_s"] % 60))))
                   if total else tx["tot_no"]]
    c.setFont("Sans", 11); c.setFillColorRGB(*(SUN if total else INK))
    c.drawString(M + 78 * mm, y + 6 * mm, lines_right[0])
    # kontakttabell
    yy = y - 1 * mm
    c.setFont("Sans", 10)
    for k in keys:
        c.setFillColorRGB(*MUTE); c.drawString(M + 78 * mm, yy, tx[k])
        c.setFillColorRGB(*INK); c.drawRightString(W - M, yy, fmt_time(shown[k], tz))
        yy -= 5.6 * mm
    y = min(y - 14 * mm, yy - 2 * mm)
    c.setFont("Sans", 8.5); c.setFillColorRGB(*MUTE); c.drawString(M, y, tx["tz"].format(tz=loc_max.tzname(), off=offs))
    y -= 8 * mm
    alt, az = circ["sun_altaz"]["max"]
    y = para(c, tx["sunpos"].format(alt=round(alt), dir=dir_txt(lang, compass8(az)), az=round(az)), M, y, W - 2 * M, size=10)
    if not total:
        y = para(c, tx["nearest"].format(km=f"{round(circ['nearest_km'], -1):,.0f}".replace(",", " " if lang != "en" else ","),
                                        dir=dir_txt(lang, compass8(circ["nearest_bearing"]))), M, y, W - 2 * M, size=10)
    # tidslinje
    y -= 8 * mm
    c.setFont("Serif", 14); c.setFillColorRGB(*INK)
    c.drawString(M, y, tx["timeline"].format(place=order["place"])); y -= 5 * mm
    c.setFont("Sans", 8.5); c.setFillColorRGB(*MUTE); c.drawString(M, y, tx["timeline_note"])
    r = 10.5 * mm
    ycen = y - 22 * mm
    step = (W - 2 * M - 2 * r) / (len(snaps) - 1)
    disks = []
    for i, s in enumerate(snaps):
        snap = dict(s)
        if FEL == "skiva_fel" and i == len(snaps) // 2:
            snap["dx_rs"] += 0.6
        x = M + r + i * step
        is_max = i == len(snaps) // 2
        draw_disk(c, x, ycen, r, snap, total=(total and is_max))
        c.setFont("Sans", 9); c.setFillColorRGB(*INK)
        c.drawCentredString(x, ycen - r - 6 * mm, fmt_time(s["utc"], tz, secs=False))
        if i in (0, len(snaps) // 2, len(snaps) - 1):
            c.setFont("Sans", 7.5); c.setFillColorRGB(*GOLD)
            c.drawCentredString(x, ycen - r - 10 * mm, {0: tx["start"], len(snaps) // 2: tx["max"]}.get(i, tx["end"]))
        disks.append({"x_pt": x, "y_pt": ycen, "r_pt": r, "max": is_max})
    geo["disks_page1"] = disks
    y = ycen - r - 20 * mm
    c.setFont("Serif", 14); c.setFillColorRGB(*INK); c.drawString(M, y, tx["what"]); y -= 6.5 * mm
    ob = circ["obscuration"]
    exp = tx["exp_tot"] if total else (tx["exp_high"] if ob >= 0.8 else tx["exp_mid"] if ob >= 0.5 else tx["exp_low"])
    y = para(c, exp, M, y, W - 2 * M, size=10)
    # stor bild av maximum
    rb = min(26 * mm, (y - 42 * mm) / 3.6)
    ybig = y - 6 * mm - rb * 1.75
    snap = dict(snaps[len(snaps) // 2])
    if FEL == "skiva_fel":
        snap["dx_rs"] += 0.6
    draw_disk(c, W / 2, ybig, rb, snap, total=total)
    c.setFont("Sans", 10); c.setFillColorRGB(*GOLD)
    c.drawCentredString(W / 2, ybig - rb * 1.75 - 4 * mm, f"{tx['max']} {fmt_time(shown['max'], tz, secs=False)}")
    geo["disk_big"] = {"x_pt": W / 2, "y_pt": ybig, "r_pt": rb, "max": True}
    footer(1); c.showPage()

    # ---------------- sida 2: karta + himmelsbild
    page_bg()
    c.setFillColorRGB(*INK); c.setFont("Serif", 18); c.drawString(M, H - 25 * mm, tx["map"])
    lat, lon = order["lat"], order["lon"]
    lon0, lon1 = min(-20, lon - 6), max(60, lon + 6)
    lat0, lat1 = min(8, lat - 5), max(64, lat + 5)
    lon0, lon1, lat0, lat1 = max(lon0, -40), min(lon1, 80), max(lat0, -25), min(lat1, 75)
    wmap = W - 2 * M
    hmap = wmap * (lat1 - lat0) / ((lon1 - lon0) * math.cos(math.radians((lat0 + lat1) / 2)))
    if hmap > 125 * mm:
        hmap = 125 * mm
    geo["map"] = draw_map(c, (M, H - 32 * mm - hmap, wmap, hmap), (lon0, lon1, lat0, lat1), band, (lat, lon), lang, FEL)
    ly = H - 38 * mm - hmap
    c.setFillColorRGB(*BAND); c.rect(M, ly - 1, 6 * mm, 3 * mm, stroke=0, fill=1)
    c.setFillColorRGB(*INK); c.setFont("Sans", 8.5); c.drawString(M + 8 * mm, ly, tx["map_band"])
    c.setFillColorRGB(1, 1, 1); c.circle(M + 3 * mm, ly - 5 * mm + 1, 1.5 * mm, stroke=0, fill=1)
    c.setFillColorRGB(*INK); c.drawString(M + 8 * mm, ly - 5 * mm, f"{tx['map_you']}: {order['place']}")
    y = para(c, tx["map_note"], M, ly - 11 * mm, W - 2 * M, size=8.5, color=MUTE)
    y -= 6 * mm
    c.setFont("Serif", 14); c.setFillColorRGB(*INK); c.drawString(M, y, tx["sky"])
    c.setFont("Sans", 8.5); c.setFillColorRGB(*MUTE); c.drawString(M, y - 5 * mm, tx["sky_note"])
    R = min(32 * mm, (y - 40 * mm) / 2 - 4 * mm)
    geo["dome"] = sky_dome(c, M + R + 6 * mm, y - 12 * mm - R, R, circ, lang)
    tx_x = M + 2 * R + 20 * mm
    yy = y - 16 * mm
    for n_, k in enumerate(("c1", "max", "c4"), 1):
        a_, z_ = circ["sun_altaz"][k]
        c.setFont("Sans", 9.5); c.setFillColorRGB(*MUTE); c.drawString(tx_x, yy, f"{n_}   {tx[k]}")
        c.setFillColorRGB(*INK)
        c.drawString(tx_x, yy - 4.6 * mm, f"{fmt_time(shown[k], tz, secs=False)}  ·  {round(a_)}°  ·  {DIRS[lang][compass8(z_)]}")
        yy -= 12 * mm
    footer(2); c.showPage()

    # ---------------- sida 3: säkerhet + förberedelse
    page_bg()
    c.setFillColorRGB(*SUN); c.setFont("Serif", 18); c.drawString(M, H - 25 * mm, tx["safety"])
    y = H - 35 * mm
    items = ["s1", "s2", "s3", "s4", "s5_tot" if total else "s5_part", "s6"]
    if FEL == "saknad_sakerhet":
        items = items[1:]
    for k in items:
        c.setFillColorRGB(*SUN); c.circle(M + 1.5 * mm, y + 1.2 * mm, 1.1 * mm, stroke=0, fill=1)
        text = tx[k]
        if FEL == "fel_sprak" and k == "s4":
            text += " Finsternis."
        y = para(c, text, M + 6 * mm, y, W - 2 * M - 6 * mm, size=10.5) - 3.5 * mm
    y -= 5 * mm
    c.setFont("Serif", 16); c.setFillColorRGB(*INK); c.drawString(M, y, tx["prep"]); y -= 8 * mm
    for k in ("p1", "p2", "p3", "p4"):
        text = tx[k].format(dir=dir_txt(lang, compass8(az)), c1=fmt_time(shown["c1"], tz, secs=False))
        c.setFillColorRGB(*GOLD); c.circle(M + 1.5 * mm, y + 1.2 * mm, 1.0 * mm, stroke=0, fill=1)
        y = para(c, text, M + 6 * mm, y, W - 2 * M - 6 * mm, size=10.5) - 3 * mm
    y -= 4 * mm
    para(c, tx["weather"], M, y, W - 2 * M, font="SerifIt", size=10, color=MUTE)
    footer(3); c.showPage()

    # ---------------- sida 4: affisch A3
    c.setPageSize(A3)
    PW, PH = A3
    page_bg()
    c.setFillColorRGB(*GOLD); c.setFont("Sans", 12); c.drawCentredString(PW / 2, PH - 32 * mm, tx["poster_date"].upper())
    c.setFillColorRGB(*INK); c.setFont("Serif", fit_font(tx["poster_title"], "Serif", 54, PW - 40 * mm))
    c.drawCentredString(PW / 2, PH - 55 * mm, tx["poster_title"])
    c.setFont("SerifIt", fit_font(order["place"], "SerifIt", 26, PW - 40 * mm)); c.drawCentredString(PW / 2, PH - 70 * mm, order["place"])
    # båge av skivor
    rr = 16 * mm
    cxp, cyp, arc_R = PW / 2, PH - 285 * mm, 128 * mm
    pdisks = []
    n = len(snaps)
    for i, s in enumerate(snaps):
        ang = math.radians(150 - 120 * i / (n - 1))
        x, yv = cxp + arc_R * math.cos(ang), cyp + arc_R * math.sin(ang)
        is_max = i == n // 2
        snap = dict(s)
        if FEL == "skiva_fel" and is_max:
            snap["dx_rs"] += 0.6
        draw_disk(c, x, yv, rr * (1.25 if is_max else 1), snap, total=(total and is_max))
        c.setFont("Sans", 11); c.setFillColorRGB(*INK)
        c.drawCentredString(x, yv - rr * (1.25 if is_max else 1) - 7 * mm, fmt_time(s["utc"], tz, secs=False))
        pdisks.append({"x_pt": x, "y_pt": yv, "r_pt": rr * (1.25 if is_max else 1), "max": is_max})
    geo["disks_poster"] = pdisks
    yb = cyp - 20 * mm
    c.setFillColorRGB(*SUN); c.setFont("Serif", 44); c.drawCentredString(PW / 2, yb, f"{pct_s} %")
    c.setFillColorRGB(*INK); c.setFont("Sans", 12); c.drawCentredString(PW / 2, yb - 9 * mm, tx["cover"])
    c.setFont("Sans", 13)
    c.drawCentredString(PW / 2, yb - 20 * mm, lines_right[0])
    row = "   ·   ".join(f"{tx[k]} {fmt_time(shown[k], tz, secs=False)}" for k in ("c1", "max", "c4"))
    c.setFont("Sans", fit_font(row, "Sans", 12, PW - 36 * mm)); c.setFillColorRGB(*GOLD); c.drawCentredString(PW / 2, yb - 30 * mm, row)
    if order.get("text"):
        c.setFont("SerifIt", fit_font(order["text"], "SerifIt", 18, PW - 40 * mm)); c.setFillColorRGB(*INK)
        c.drawCentredString(PW / 2, 60 * mm, order["text"])
    coord = f"{abs(lat):.4f}° {'N' if lat >= 0 else 'S'}  ·  {abs(lon):.4f}° {'E' if lon >= 0 else 'W'}"
    c.setFont("Sans", 10); c.setFillColorRGB(*MUTE); c.drawCentredString(PW / 2, 50 * mm, coord)
    c.setFont("Sans", 6.8)
    yy = 22 * mm
    for ln in wrap(tx["credit"], "Sans", 6.8, PW - 40 * mm):
        c.drawCentredString(PW / 2, yy, ln); yy -= 9
    c.showPage(); c.save()
    return geo, {"pct_s": pct_s, "times": {k: fmt_time(shown[k], tz) for k in keys}}


def generate(order_path):
    timings = {}
    t0 = time.perf_counter()
    order = json.load(open(order_path, encoding="utf-8"))
    sky = Sky()
    timings["ladda_data_s"] = time.perf_counter() - t0
    t1 = time.perf_counter()
    lat, lon = order["lat"], order["lon"]
    if FEL == "berakning_fel":
        lon = lon + 1.0
    circ = local_circumstances(sky, lat, lon, order.get("elevation_m", 0.0))
    if circ["type"] == "none" or circ["sun_altaz"]["max"][0] < 0:
        raise SystemExit("eclipse_not_visible")
    band = totality_band(sky)
    circ["nearest_km"], circ["nearest_bearing"] = (0.0, 0.0) if circ["type"] == "total" else nearest_totality(band, lat, lon)
    snaps = timeline(sky, lat, lon, circ)
    timings["berakna_s"] = time.perf_counter() - t1
    out = Path(os.environ.get("STJARN_OUT", ROOT / "ut")); out.mkdir(parents=True, exist_ok=True)
    files, geos, shown = {}, {}, {}
    t2 = time.perf_counter()
    for lang in order["languages"]:
        path = out / f"{order['id']}_{lang}.pdf"
        geos[lang], shown[lang] = render(order, circ, snaps, band, lang, path)
        files[lang] = str(path)
    timings["rendera_s"] = time.perf_counter() - t2
    meta = {"generator": GENERATOR_VERSION, "product": "formorkelse", "order": order, "files": files,
            "circumstances": circ, "timeline": snaps, "geometry": geos, "shown": shown, "timings": timings,
            "sha256": {l: hashlib.sha256(open(p, "rb").read()).hexdigest() for l, p in files.items()}}
    json.dump(meta, open(out / f"{order['id']}_meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return meta


if __name__ == "__main__":
    m = generate(sys.argv[1])
    print(json.dumps({"files": m["files"], "timings": m["timings"], "type": m["circumstances"]["type"],
                      "obscuration": m["circumstances"]["obscuration"]}, indent=1))
