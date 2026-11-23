"""Quiet's original line icons, extended for the IDE; native images are owned."""
from functools import lru_cache

try:
    from PIL import Image, ImageDraw
    from friday_chat.icons import IconBank as ChatIcons, _PanelImage
except ImportError:
    # Retain the pure-codec/no-Pillow regression path. Production GUI
    # requirements declare Pillow; fallback buttons keep text names.
    Image = ImageDraw = ChatIcons = _PanelImage = None

CHAT_NAMES = {'spark','chat','plus','arrow_up','download','more','clip','copy',
              'trash','close','info','file','lock','sun','mic','speaker','sidebar','pen'}
ALIASES = {'explorer':'files','git':'branch','run':'play','gear':'settings',
           'panel-side':'sidebar','ellipsis':'more','new-file':'file_plus',
           'new-folder':'folder_plus','chevron-down':'chevron_down',
           'chevron-up':'chevron_up','logo':'spark','panel-bottom':'terminal'}
FALLBACKS = {'play':'▷','stop':'□','search':'⌕','folder':'▱','files':'▤',
             'branch':'⑂','settings':'⚙','terminal':'>_','spark':'✧','chat':'▤',
             'plus':'+','close':'×','more':'…','arrow_up':'↑','clip':'↗',
             'refresh':'↻','collapse':'⌃','chevron_down':'⌄','chevron_up':'⌃',
             'file_plus':'+','folder_plus':'+','extensions':'▦','minimize':'−',
             'maximize':'□','account':'○','check':'✓','warning':'!', 'clock':'◷'}


@lru_cache(maxsize=128)
def raster(name, color, size):
    unit = size * 4 / 24
    image = Image.new('RGBA', (size*4, size*4))
    d = ImageDraw.Draw(image)
    width = max(1, round(1.6*unit))
    def line(points, closed=False):
        pts = [(x*unit,y*unit) for x,y in points]
        if closed: pts.append(pts[0])
        d.line(pts,fill=color,width=width,joint='curve')
        for x,y in pts:
            r=width/2
            d.ellipse((x-r,y-r,x+r,y+r),fill=color)
    def rect(box, radius=2):
        d.rounded_rectangle(tuple(v*unit for v in box),radius=radius*unit,
                            outline=color,width=width)
    def oval(box): d.ellipse(tuple(v*unit for v in box),outline=color,width=width)
    if name=='search':
        oval((3,3,16,16));line([(15,15),(21,21)])
    elif name=='files':
        rect((7,7,21,21));line([(3,16),(3,3),(16,3)])
    elif name in {'folder','folder_plus'}:
        line([(3,7),(3,4),(9,4),(12,7),(21,7),(21,20),(3,20),(3,7)],True)
        if name=='folder_plus':line([(9,13),(15,13)]);line([(12,10),(12,16)])
    elif name=='file_plus':
        line([(14,3),(4,3),(4,21),(20,21),(20,9),(14,3)],True)
        line([(14,3),(14,9),(20,9)]);line([(9,15),(15,15)]);line([(12,12),(12,18)])
    elif name=='branch':
        oval((4,2,10,8));oval((4,17,10,23));oval((15,5,21,11))
        line([(7,8),(7,17)]);line([(18,11),(18,14),(7,14)])
    elif name=='play':line([(7,4),(20,12),(7,20)],True)
    elif name=='stop':rect((6,6,18,18),2)
    elif name=='settings':
        import math
        points=[]
        for i in range(24):
            a=math.pi*i/12;r=9 if i%3 in (0,1) else 7
            points.append((12+r*math.cos(a),12+r*math.sin(a)))
        line(points,True);oval((9,9,15,15))
    elif name=='terminal':
        rect((2,4,22,20),3);line([(6,9),(9,12),(6,15)]);line([(12,16),(17,16)])
    elif name=='extensions':
        for b in ((3,3,10,10),(3,14,10,21),(14,14,21,21),(15,2,22,9)):rect(b,1)
    elif name=='refresh':
        d.arc(tuple(v*unit for v in (4,4,20,20)),45,320,fill=color,width=width)
        line([(17,3),(21,7),(16,8)])
    elif name=='collapse':line([(6,13),(12,7),(18,13)]);line([(5,19),(19,19)])
    elif name=='chevron_down':line([(6,9),(12,15),(18,9)])
    elif name=='chevron_up':line([(6,15),(12,9),(18,15)])
    elif name=='minimize':line([(6,12),(18,12)])
    elif name=='maximize':rect((5,5,19,19),1)
    elif name=='account':oval((8,3,16,11));d.arc(tuple(v*unit for v in (4,13,20,27)),180,360,fill=color,width=width)
    elif name=='check':line([(5,12),(10,17),(20,6)])
    elif name=='warning':line([(12,3),(22,21),(2,21)],True);line([(12,8),(12,14)]);oval((11.5,17,12.5,18))
    elif name=='clock':oval((3,3,21,21));line([(12,7),(12,12),(16,14)])
    else:raise ValueError('Unknown IDE icon: '+name)
    return image.resize((size*2,size*2),Image.Resampling.LANCZOS)


class IconBank:
    def __init__(self, master):
        self.master=master
        self.chat=ChatIcons(master) if ChatIcons is not None else None
        self.images={}
    def __call__(self,name,color='#B2B1B8',size=18):
        name=ALIASES.get(name,name)
        if self.chat is None:return None
        if name in CHAT_NAMES:return self.chat(name,color,size)
        key=(name,color,size)
        if key not in self.images:self.images[key]=_PanelImage(self.master,raster(*key),size)
        return self.images[key]
    def clear(self):
        if self.chat is not None:self.chat.clear()
        for image in self.images.values():
            image._scaled_dark_photo_images.clear();image._scaled_light_photo_images.clear()
        self.images.clear()
