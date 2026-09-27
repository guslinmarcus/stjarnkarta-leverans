"""Vägnät och ruttberäkning för hitta-hit-kartan (brollopskarta.py): bygger en enkel graf av OSM-vägarna
i utsnittet (F["roads"] ur osmdata.parse_features, alltså samma Geofabrik-data som ritas) och beräknar
kortaste väg mellan två punkter med Dijkstra, viktad på verklig längd (haversine mellan varje vägsegments
ändpunkter – två segment som delar exakt samma koordinat räknas som samma korsning, precis som OSM:s noder).

Tid: bilhastighet per vägklass (schabloner nedan, ingen mätning – körfältet är öppet för att ersättas med
uppmätta NVDB-hastigheter senare), gångtid med en konstant gånghastighet oavsett vägklass.

Grinden (grind_brollopskarta.py) bygger sin EGEN graf och kör sin EGEN Dijkstra oberoende av den här filen,
så ett fel i den här modulen upptäcks av grinden i stället för att döljas av den.
"""
import heapq
import math

R_EARTH_M = 6371008.8
WALK_KMH = 4.8  # normal gångfart på plan, jämn mark
SPEED_KMH = {"motorway": 100, "trunk": 90, "primary": 70, "secondary": 60, "tertiary": 50,
             "unclassified": 40, "residential": 30, "living_street": 15, "pedestrian": 5, "road": 40}


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * R_EARTH_M * math.asin(math.sqrt(min(1.0, h)))


def _key(lon, lat):
    return (round(lon, 6), round(lat, 6))


def build_graph(roads):
    """roads = F['roads'] = [(klass, [(lon,lat), …]), …] (osmdata.parse_features). Ohindrad, dubbelriktad graf."""
    graph = {}
    for cls, pts in roads:
        for (lo1, la1), (lo2, la2) in zip(pts, pts[1:]):
            if lo1 == lo2 and la1 == la2:
                continue
            a, b = _key(lo1, la1), _key(lo2, la2)
            d = haversine_m(la1, lo1, la2, lo2)
            if d <= 0:
                continue
            graph.setdefault(a, []).append((b, d, cls))
            graph.setdefault(b, []).append((a, d, cls))
    return graph


def nearest_node(graph, lat, lon):
    """Enkel linjär sökning (utsnittet är litet – en bröllopskarta, aldrig ett helt land)."""
    best, bd = None, None
    for lo, la in graph:
        d = haversine_m(lat, lon, la, lo)
        if bd is None or d < bd:
            best, bd = (lo, la), d
    return best, bd


def shortest_path(graph, start, end):
    """Dijkstra på vägnätet. start/end = nodnycklar redan i grafen.
    Returnerar dict(dist_m, path=[(lon,lat), …], drive_s, walk_s) eller None om ingen väg finns."""
    if start not in graph or end not in graph:
        return None
    dist = {start: 0.0}
    prev = {}
    pq = [(0.0, start)]
    seen = set()
    while pq:
        d, u = heapq.heappop(pq)
        if u in seen:
            continue
        seen.add(u)
        if u == end:
            break
        for v, w, cls in graph.get(u, []):
            nd = d + w
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                prev[v] = (u, w, cls)
                heapq.heappush(pq, (nd, v))
    if end not in dist:
        return None
    path = [end]
    drive_s = 0.0
    cur = end
    while cur != start:
        u, w, cls = prev[cur]
        drive_s += w / (SPEED_KMH.get(cls, 40) * 1000 / 3600)
        path.append(u)
        cur = u
    path.reverse()
    total = dist[end]
    walk_s = total / (WALK_KMH * 1000 / 3600)
    return {"dist_m": total, "path": path, "drive_s": drive_s, "walk_s": walk_s}


def route(roads, a_latlon, b_latlon):
    """Hela kedjan: bygg graf, snäpp fast punkterna, kör Dijkstra. Returnerar None om ingen väg hittas
    eller om närmaste vägnod ligger orimligt långt (> 400 m) från punkten (dålig vägtäckning i utsnittet)."""
    graph = build_graph(roads)
    if not graph:
        return None
    na, da = nearest_node(graph, *a_latlon)
    nb, db = nearest_node(graph, *b_latlon)
    if na is None or nb is None or da > 400.0 or db > 400.0:
        return None
    r = shortest_path(graph, na, nb)
    if r is None:
        return None
    r["snap_a_m"] = round(da, 1)
    r["snap_b_m"] = round(db, 1)
    return r
