# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110 - 1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

bl_info = {
        "name": "ObLib",
        "description":"Object Librarian",
        "author":"Shams Kitz",
        "version":(0,2),
        "blender":(2,80,0),
        "location":"3d Viewport UI",
        "warning":"",
        "wiki_url":"",
        "tracker_url":"https://github.com/dustractor/oblib.git",
        "category": "Object"
        }

import sqlite3
import pathlib
import bpy


def _(c=None,r=[]):
    if c:
        r.append(c)
        return c
    return r


class ObjectsLibrarian(sqlite3.Connection):
    def __init__(self,name,**kwargs):
        super().__init__(name,**kwargs)
        self.cu = self.cursor()
        self.cu.row_factory = lambda c,r:r[0]
        self.executescript(
        """
        pragma foreign_keys=ON;
        pragma recursive_triggers=ON;

        create table if not exists paths (
        id integer primary key,
        name text,
        mtime real,
        unique (name) on conflict replace);

        create table if not exists blends (
        id integer primary key,
        path_id integer,
        name text,
        mtime real,
        unique (name) on conflict replace,
        foreign key (path_id) references paths(id) on delete cascade);

        create table if not exists objects (
        id integer primary key,
        blend_id integer,
        name text,
        unique (name) on conflict replace,
        foreign key (blend_id) references blends(id) on delete cascade);

        create table if not exists active_path (
        state integer default 0,
        path_id integer,
        unique (state) on conflict replace,
        foreign key (path_id) references paths(id) on delete cascade);

        create view if not exists objects_view as
        select id,name from objects where blend_id in (select id from blends
        where path_id=(select path_id from active_path where state=0));

        """)
        self.commit()
    def path(self,path_id):
        return self.cu.execute(
                "select name from paths where id=?",
                (path_id,)).fetchone()
    @property
    def paths(self):
        yield from self.execute("select id,name from paths")
    def add_path(self,path):
        self.active_path = self.execute(
                "insert into paths (name) values (?)", (path,)).lastrowid
    @property
    def active_path(self):
        return self.cu.execute(
                "select path_id from active_path where state=0").fetchone()
    @active_path.setter
    def active_path(self,path_id):
        self.execute("insert into active_path (path_id) values (?)",
                (path_id,))
        self.prune_gone_blends(path_id)
        self.add_blends_in_path(path_id)
        self.commit()
    @property
    def objects(self):
        yield from self.execute("select id,name from objects_view")
    def object_names(self):
        return self.cu.execute("select name from objects_view").fetchall()
    def add_blends_in_path(self,path_id):
        path = self.cu.execute(
                "select name from paths where id=?",
                (path_id,)).fetchone()
        for bpath in pathlib.Path(path).glob("**/*.blend"):
            blend = str(bpath)
            cur_bmtime = bpath.stat().st_mtime
            already_in = self.cu.execute(
                    "select count(*) from blends where name=?",
                    (blend,)).fetchone()
            if already_in:
                lib_bmtime = self.cu.execute(
                        "select mtime from blends where name=?",
                        (blend,)).fetchone()
                if lib_bmtime == cur_bmtime:
                    print(f"skipping {blend}: modification time was the same.")
                    continue
            blend_id = self.execute(
                    "insert into blends (path_id,name,mtime) values (?,?,?)",
                    (path_id,blend,cur_bmtime)).lastrowid
            with bpy.data.libraries.load(blend) as (data_from,data_to):
                blend_objcount = len(data_from.objects)
                for objname in data_from.objects:
                    self.execute(
                        "insert into objects (blend_id,name) values (?,?)",
                        (blend_id,objname))
            word = ["objects","object"][blend_objcount==1]
            print(f"added {blend} with {blend_objcount} {word}.")
    def prune_gone_blends(self,path_id):
        for i,b in self.execute("select id,name from blends where path_id=?",
                (path_id,)):
            if not pathlib.Path(b).exists():
                print(f"Removed trace of {b}")
                self.execute("delete from blends where id=?",(i,))


class ObjectsLibrary:
    _handle = None
    @property
    def cx(self):
        if not self._handle:
            self._handle = sqlite3.connect(
                    bpy.utils.user_resource(
                        "CONFIG",
                        path="oblib.db"),
                    factory=ObjectsLibrarian)
        return self._handle


db = ObjectsLibrary()


@_
class OBLIB_OT_send_object(bpy.types.Operator):
    bl_idname = "oblib.send_object"
    bl_label = "Send Object to Library"
    bl_description = "Send active object to the active object library"
    bl_options = {"INTERNAL"}
    force_overwrite: bpy.props.BoolProperty(
            default=False,
            name="Overwrite existing Object with same name?",
            description="Check this box and choose OK to overwrite the "
                        "previously existing object of same name.")
    objfile: bpy.props.StringProperty(default="",
            name="or rename it",
            description="Edit to change the name of the file in which "
                        "the object will be kept.")
    def draw(self,context):
        self.layout.box().prop(self,"force_overwrite")
        self.layout.box().prop(self,"objfile")
    @classmethod
    def poll(self,context):
        return all((context.object,db.cx.active_path))
    def invoke(self,context,event):
        path_id = db.cx.active_path
        path = db.cx.cu.execute("select name from paths where id=?",
                (path_id,)
                ).fetchone()
        objfile_path = pathlib.Path(path)/f"{context.object.name}.blend"
        self.objfile = str(objfile_path)
        if objfile_path.exists():
            context.window_manager.invoke_props_dialog(self,width=720)
            return {"RUNNING_MODAL"}
        else:
            return self.execute(context)
    def execute(self,context):
        objfile_path = pathlib.Path(self.objfile)
        if not objfile_path.exists() or self.force_overwrite:
            bpy.data.libraries.write(
                    str(objfile_path),
                    set([context.object]),
                    fake_user=True)
        db.cx.active_path = db.cx.active_path
        return {"FINISHED"}


@_
class OBLIB_OT_load_object(bpy.types.Operator):
    bl_idname = "oblib.load_object"
    bl_label = "Load Object"
    bl_options = {"INTERNAL"}
    obj_id: bpy.props.IntProperty()
    def execute(self,context):
        print("loading object",self.obj_id)
        blend,objname = db.cx.execute(
                """select blends.name,objects.name from objects
                join blends on blends.id=objects.blend_id
                where objects.id=? """,
                (self.obj_id,)).fetchone()
        with bpy.data.libraries.load(blend) as (data_from,data_to):
            data_to.objects = [objname]
        obj = data_to.objects[0]
        context.scene.collection.objects.link(obj)
        loc_v,rot_q,scale_v = context.scene.cursor.matrix.decompose()
        rot_v = rot_q.to_euler()
        obj.location,obj.rotation_euler,obj.scale = loc_v,rot_v,scale_v
        #now what
        return {"FINISHED"}


@_
class OBLIB_OT_select_path(bpy.types.Operator):
    bl_idname = "oblib.select_path"
    bl_label = "Select Path"
    bl_options = {"INTERNAL"}
    bl_description = "hold shift to open in file browser"
    path_id: bpy.props.IntProperty()
    def invoke(self,context,event):
        if event.shift:
            path = db.cx.path(self.path_id)
            if path:
                bpy.ops.wm.path_open(filepath=path)
                return {"FINISHED"}
            else:
                return {"CANCELLED"}
        elif event.alt:
            db.cx.execute("delete from paths where id=?",(self.path_id,))
            db.cx.commit()
            return {"FINISHED"}
        return self.execute(context)
    def execute(self,context):
        print("selecting path",self.path_id)
        db.cx.active_path = self.path_id
        return {"FINISHED"}


@_
class OBLIB_OT_add_path(bpy.types.Operator):
    bl_idname = "oblib.add_path"
    bl_label = "Add Library Path..."
    bl_options = {"INTERNAL"}
    directory: bpy.props.StringProperty(
            subtype="DIR_PATH",
            maxlen=1024,
            default="")
    def invoke(self,context,event):
        if not self.directory or (
                self.directory and not pathlib.Path(self.directory).is_dir()
                ):
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}
        else:
            return self.execute(context)
    def execute(self,context):
        print("self.directory:",self.directory)
        db.cx.add_path(self.directory)
        return {"FINISHED"}


@_
class OBLIB_MT_path_menu(bpy.types.Menu):
    bl_label = "Add/Change Library"
    bl_description = "Choose/add Library Paths"
    def draw(self,context):
        layout = self.layout
        ap = db.cx.active_path
        for oid,path in db.cx.paths:
            layout.operator(
                    "oblib.select_path",
                    text=path,
                    icon=["FILE_FOLDER","BOOKMARKS"][oid==ap]).path_id = oid
        layout.separator()
        layout.operator("oblib.add_path").directory = ""


@_
class OBLIB_MT_objs_menu(bpy.types.Menu):
    bl_label = "Object Selector"
    bl_description = "Menu of Objects"
    def draw(self,context):
        i = -1
        for i,obj in db.cx.objects:
            self.layout.operator(
                "oblib.load_object",
                text=obj,
                icon=["TRIA_RIGHT","FF"][obj in bpy.data.objects]).obj_id = i
        if i == -1 and db.cx.active_path:
            self.layout.label(
                text="No objects found in any blends in path:%s!"%db.cx.path(
                    db.cx.active_path))


@_
class OBLIB_MT_main_menu(bpy.types.Menu):
    bl_label = "Librarian"
    bl_description = "ObLib Main Menu"
    def draw(self,context):
        if context.active_object != None:
            has_name = context.active_object.name in db.cx.object_names()
        else:
            has_name = False
        op = self.layout.operator(
                "oblib.send_object",
                emboss=not has_name,
                icon=["FORWARD","FILE_TICK"][has_name])
        self.layout.menu(
                "OBLIB_MT_objs_menu",
                icon="PLUGIN")
        self.layout.menu(
                "OBLIB_MT_path_menu",
                icon="FILEBROWSER")


def header_draw(self,context):
    if context.area.spaces.active.context == "OBJECT":
        layout = self.layout.row(align=True)
        layout.menu("OBLIB_MT_main_menu",text="",icon="THREE_DOTS")

def register():
    list(map(bpy.utils.register_class,_()))
    km = bpy.context.window_manager.keyconfigs.addon.keymaps.new(
        "3D View",space_type="VIEW_3D")
    kmi = km.keymap_items.new(
        "WM_OT_call_menu","A","PRESS",ctrl=True,alt=True,shift=True)
    kmi.properties.name = "OBLIB_MT_objs_menu"
    kmi = km.keymap_items.new(
        "OBLIB_OT_send_object","Z","PRESS",ctrl=True,alt=True,shift=True)

    bpy.types.PROPERTIES_HT_header.append(header_draw)
    ap = db.cx.active_path
    if ap:
        db.cx.prune_gone_blends(ap)
        db.cx.commit()

def unregister():
    bpy.utils.previews.remove(icons_d)
    bpy.types.PROPERTIES_PT_navigation_bar.remove(header_draw)
    list(map(bpy.utils.unregister_class,_()))

