"""Plain-English descriptions of the game's textures, so Browse search finds them by what they show:
"pikachu body", "turtleneck", "hotel manager", "poke ball icon", "building wall", "donut".

describe(rel) works from the file's folder and name: who or what it belongs to (Pokémon, clothing item,
character, map area, item icon...), which part (body, eyes, shoes...) and what the texture does (colour,
bumps, colour regions...). Names come from the game's own text (Pokémon, items, clothing).
"""
import os
import re
import unicodedata

import za_formats as zf

# what a texture does, from the end of its name (only endings whose meaning is clear)
ROLES = [
    ('fresnel_alb', 'edge colour'), ('fresnel_nrm', 'edge bumps'), ('fresnel_prb', 'edge reflection'),
    ('rim_msk', 'rim light mask'), ('spe_msk', 'shine mask'), ('black_msk', 'dark parts mask'),
    ('white_msk', 'light parts mask'), ('plx_msk', 'depth (parallax) mask'), ('eye_msk', 'eye mask'),
    ('emm', 'glow mask'), ('alb', 'colour'), ('nrm', 'bumps (normal map)'), ('ao', 'soft shadows (ambient occlusion)'),
    ('lym', 'colour regions (layer mask)'), ('emv', 'glow'), ('mtl', 'metal'), ('prb', 'reflection'),
    ('lgt', 'baked light'), ('msk', 'mask'),
]
CLOTHING = {'upb': 'top', 'lwb': 'bottoms', 'drs': 'outfit', 'hdw': 'headwear', 'eyw': 'eyewear', 'glv': 'gloves',
            'lgw': 'legwear', 'ftw': 'footwear', 'bag': 'bag', 'acc': 'accessory', 'hrs': 'hairstyle', 'face': 'face',
            'bng': 'bangle'}
MAP_KIND = {'ar': 'area', 'b': 'building', 'g': 'ground', 'i': 'interior', 'entrance': 'entrance', 'lmg': 'landmark'}
# words in map names that are Japanese (romaji) or names: shown with their English meaning
MAP_TRANSLATE = {
    'kanban': 'signboard', 'kaisatsu': 'ticket gate', 'enkei': 'distant scenery', 'komono': 'small props',
    'futsu': 'regular', 'yukari': 'edge', 'furadari': "Lysandre's", 'doomira': 'Bronzor', 'hisuinura': 'Hisuian Sneasel',
    'dairiseki': 'marble', 'danbooru': 'cardboard', 'doro': 'mud', 'enbi': 'vinyl', 'furooringu': 'flooring',
    'garasu': 'glass', 'hana': 'flowers', 'kiiro': 'yellow', 'ishidatami': 'paving stones', 'iwa': 'rock',
    'jari': 'gravel', 'kawara': 'roof tiles', 'ki': 'wood', 'karui': 'light', 'konkuriito': 'concrete',
    'koori': 'ice', 'kusamura': 'grass', 'kare': 'withered', 'yuki': 'snow', 'maguma': 'magma', 'mizu': 'water',
    'ike': 'pond', 'kawa': 'river', 'mizutamari': 'puddle', 'umi': 'sea', 'purasutikku': 'plastic', 'ragu': 'rug',
    'shiba': 'lawn', 'suna': 'sand', 'sabaku': 'desert', 'sunahama': 'beach', 'takenoha': 'bamboo leaves',
    'tetsu': 'iron', 'tsuchi': 'soil', 'katai': 'hard', 'attr': 'ground surface',
}
# extra words search also matches (not shown): related words and probable meanings of codes
SEARCH_EXTRA = {
    'kanban': 'sign billboard advert', 'neon': 'sign billboard', 'monitor': 'screen display billboard',
    'display': 'screen', 'hologram': 'holo screen billboard', 'publication': 'poster magazine',
    'picture': 'painting poster', 'pictureframe': 'painting poster frame', 'flag': 'banner', 'awning': 'canopy shade',
    'mobl': 'generic background building', 'mobof': 'generic background office building', 'clod': 'distant city lod',
    'lmg': 'light map', 'enkei': 'background landscape far', 'furadari': 'lysandre cafe',
    'pokemon_center': 'pokecenter',
}
UI = {'icon_donut': 'donut icon', 'map_pic': 'map picture', 'tips': 'tips picture', 'logo_brand': 'clothing brand logo',
      'chara': 'character portrait', 'ryl_npc': 'battle royale character portrait', 'photomode_pose': 'photo mode pose icon',
      'onepicture': 'story picture', 'icon_knr_big': 'big icon', 'icon_pokemon_last': 'Pokémon icon',
      'hud_spmg': 'Pokémon picture', 'poke_spmg': 'Pokémon picture', 'hud_shopping': 'clothing shop picture',
      'icon_item': 'item icon'}


def _words(s):
    """'t1_b_tenant_building_wall_c' -> 'tenant building wall c' (codes and numbers dropped)."""
    out = [w for w in re.split(r'[_\-]+', s) if w and not re.fullmatch(r'\d+|[a-z]{0,3}\d{2,}[a-z]?|[a-z]?\d[a-z]?', w)]
    return ' '.join(out)


def map_words(s):
    """Map object words with English for the Japanese ones: 'pokemon center kanban' -> '... kanban (signboard)'."""
    out = []
    for w in _words(s).split():
        if len(w) == 1:
            continue                      # leftover single-letter codes (t1_b_..., t1_i_...)
        out.append('%s (%s)' % (w, MAP_TRANSLATE[w]) if w in MAP_TRANSLATE else w)
    return ' '.join(out)


def plain(s):
    """Lower case without accents, for searching ("Poké Ball" -> "poke ball")."""
    return ''.join(c for c in unicodedata.normalize('NFKD', s.lower()) if not unicodedata.combining(c))


def role_of(stem):
    for end, what in ROLES:
        if stem == end or stem.endswith('_' + end):
            return what, stem[:-len(end)].rstrip('_')
    return '', stem


class Describer:
    def __init__(self, root, names=None):
        self.root = root
        names = names or []
        self.pokemon = {e.pattern: e.name for e in names if e.kind.startswith('Pok') and e.pattern}
        self.clothing = {e.pattern: e.name for e in names if e.kind == 'Clothing' and e.pattern}
        self.items, self.cloth_ids = {}, {}
        base = os.path.join(root, 'ik_message', 'dat', 'English', 'common')
        try:
            with open(os.path.join(base, 'itemname.dat'), 'rb') as f:
                self.items = dict(enumerate(zf.decode_msg(f.read())))
        except (OSError, ValueError):
            pass
        try:
            import za_names as zn
            keys = {}
            for e in names:
                if e.kind == 'Clothing' and getattr(e, 'key', None):
                    keys[e.key] = e.name
            for id_, key, thumb in zn._dressup_rows(root):
                if key in keys:
                    self.cloth_ids[id_] = keys[key]
        except Exception:  # noqa: BLE001 - names are a nicety
            pass

    def pokemon_name(self, num):
        return self.pokemon.get('pm%04d' % num) or 'Pokémon pm%04d' % num

    def search_text(self, rel, desc):
        """What search matches: the description, plus words that help but would clutter the list."""
        r = rel.replace('\\', '/').lower()
        extra = ' pokemon' if r.startswith(('ik_pokemon/', 'ui/tex/hud_spmg', 'ui/tex/poke_spmg')) else ''
        if r.startswith('field/'):
            name = os.path.basename(r)
            extra += ''.join(' ' + v for k, v in SEARCH_EXTRA.items() if k in name)
        return plain(desc) + extra

    def describe(self, rel):
        """Description of a texture by its game path (forward slashes), or '' if nothing useful is known."""
        rel = rel.replace('\\', '/').lower()
        parts = rel.split('/')
        stem = os.path.splitext(parts[-1])[0]
        role, rest = role_of(stem)
        tail = (' · ' + role) if role else ''
        try:
            return self._describe(parts, stem, rest, tail)
        except Exception:  # noqa: BLE001
            return ''

    def _describe(self, parts, stem, rest, tail):
        top = parts[0]
        # Pokémon models: ik_pokemon/data/pm0025/pm0025_00_00/pm0025_00_00_body_a_alb.bntx
        m = re.match(r'pm(\d{4})_(\d\d)_(\d\d)_?(.*)$', rest)
        if top == 'ik_pokemon' and m:
            form = int(m.group(2))
            what = self.pokemon_name(int(m.group(1)))
            if form:
                what += ' form %d' % form
            part = _words(m.group(4))
            if 'icon' in parts[:-1]:
                part = (part + ' icon').strip()
            return '%s%s%s' % (what, (' · ' + part) if part else '', tail)
        if top == 'ik_pokemon':
            return 'Pokémon shared texture · %s%s' % (_words(rest), tail)
        if top == 'ik_chara':
            return self._chara(parts, rest, tail)
        if top == 'field':
            return self._field(parts, rest, tail)
        if top == 'ui':
            return self._ui(parts, stem)
        if top == 'light':
            return 'lighting data · %s' % _words(parts[-2] if len(parts) > 1 else stem)
        if top == 'ik_demo':
            return 'cutscene texture · %s%s' % (_words(rest), tail)
        if top == 'ik_effect':
            return 'effect texture · %s%s' % (_words(rest), tail)
        if top == 'system_resource':
            return 'system texture · %s' % _words(stem)
        return ''

    def _chara(self, parts, rest, tail):
        kind = parts[1] if len(parts) > 1 else ''
        folder = parts[2] if len(parts) > 2 else ''
        if kind == 'model_pc':
            m = re.match(r'p(\d)_([a-z]+)(\d{4}_\d\d_[a-z0-9]+)$', folder)
            if m:
                cat = CLOTHING.get(m.group(2), m.group(2))
                name = self.clothing.get(m.group(2) + m.group(3)) or _words(folder.split('_', 3)[-1])
                part = _words(re.sub(r'^p\d_[a-z]+\d{4}_\d\d_?', '', rest))
                if part in (cat, m.group(2)):
                    part = ''
                style = ' (body style %s)' % m.group(1) if m.group(1) in '12' else ''
                return 'clothing: %s · %s%s%s%s' % (name, cat, style, (' · ' + part) if part else '', tail)
            return 'player: %s%s' % (_words(folder), tail)
        if kind == 'model_pc_base':
            return 'player body · %s%s' % (_words(rest), tail)
        if kind in ('model_cc_vr', 'model_cc_ir'):
            sub = parts[3] if kind == 'model_cc_vr' and len(parts) > 3 else folder
            m = re.match(r'[a-z]+_([a-z]+)\d{4}_\d\d_(.+)$', sub)
            if m and kind == 'model_cc_vr':
                who = _words(m.group(2)).replace(' f', ' female').replace(' m', ' male')
                return 'townsperson outfit: %s · %s%s' % (CLOTHING.get(m.group(1), m.group(1)), who, tail)
            return 'character: %s · %s%s' % (_words(sub), _words(rest.split('_', 2)[-1]), tail)
        if kind == 'model_uq':
            return 'character: %s · %s%s' % (_words(folder), _words(rest.split('_', 2)[-1]), tail)
        if kind == 'model_ob':
            m = re.search(r'pm(\d{4})_', rest)
            who = self.pokemon_name(int(m.group(1))) if m else _words(folder)
            return 'object / character: %s · %s%s' % (who, _words(rest.split('_', 3)[-1]), tail)
        if kind == 'share':
            return 'shared character material · %s%s' % (_words(rest), tail)
        if kind.startswith('motion'):
            return 'character animation texture · %s%s' % (_words(folder), tail)
        return 'character · %s%s' % (_words(rest), tail)

    def _field(self, parts, rest, tail):
        kind = parts[1] if len(parts) > 1 else ''
        if kind == 'share_tex':
            return 'map surface (shared) · %s%s' % (map_words(rest), tail)
        if kind == 'env':
            return 'map environment data · %s' % _words(rest)
        if kind == 'model' and len(parts) > 2:
            if parts[2] == 'env':
                return 'placed map object · %s%s' % (map_words(rest), tail)
            area = parts[2]
            sub = parts[3] if len(parts) > 3 else ''
            k = sub.split('_', 1)[1] if '_' in sub else ''
            what = MAP_KIND.get(re.sub(r'\d+$', '', k), 'map')
            name = map_words(rest)
            return 'map %s (%s): %s%s' % (what, area, name, tail)
        return 'map · %s%s' % (map_words(rest), tail)

    def _ui(self, parts, stem):
        kind = parts[2] if len(parts) > 2 and parts[1] == 'tex' else (parts[1] if len(parts) > 1 else '')
        label = UI.get(kind, 'menu graphic')
        m = re.search(r'item_(\d{4})', stem)
        if kind == 'icon_item' and m:
            n = self.items.get(int(m.group(1)), '')
            return 'item icon: %s' % (n or 'item %d' % int(m.group(1)))
        m = re.search(r'pm(\d{4})_(\d\d)', stem)
        if m:
            return '%s: %s%s' % (label, self.pokemon_name(int(m.group(1))),
                                 ' form %d' % int(m.group(2)) if int(m.group(2)) else '')
        m = re.search(r'dressup_(\d{7,8})', stem)
        if m:
            n = self.cloth_ids.get(int(m.group(1)), '')
            return '%s: %s' % (label, n or 'clothing %s' % m.group(1))
        m = re.match(r'donut_([a-z]+)', stem)
        if m:
            return 'donut icon: %s' % m.group(1)
        return '%s · %s' % (label, _words(stem))
