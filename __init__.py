"""
Blender LAMMPS text-output (dump) importer for visulization – Blender 5.0 compatible

The script imports atom coordinates into Blender as spheres.

Expected dump layout (space-separated):
ITEM: ATOMS_id  type  x  y  z  radius

If radius column is missing the fallback radius is used.

"""

from __future__ import annotations                     # for type hinting
bl_info = {
    "name": "LAMMPS atoms with radius (.lammpstrj)",
    "author": "quarkKnight – updated for Blender 5.0",
    "blender": (5, 0, 0),
    "version": (0, 3, 0),
    "location": "File > Import > LAMMPS atoms (.lammpstrj, .dump)",
    "description": "Import LAMMPS dump with per-atom radius as spheres",
    "category": "Import-Export",
}

# from __future__ import annotations                      # for type hinting
from pathlib import Path                                # for modern file handling, better than os.path
import random                                           # for random atom colors
import bpy                                              # Blender Python API
from bpy.types import Operator                          # Base class for custom Blender operators.
from bpy_extras.io_utils import ImportHelper            # Provides file selector UI
from bpy.props import StringProperty, FloatProperty     # Defines UI properties for operators

# ------------------------------------------------------------
# Utility Functions
# ------------------------------------------------------------

def ensure_material(name:str, rgb: tuple[float, float, float] )->bpy.data.materials:
    """
    Ensure a material with the given name and color exists, and return it.
    If the material already exists, it will be returned as is.
    ----------
    Args:
        name (str): The name of the material to ensure.
        rgb (tuple[float, float, float]): A tuple of (R, G, B) values for the material color, each in the range [0.0, 1.0].
    Returns:
        bpy.data.materials: The material object with the specified name and color.
    """

    mat = bpy.data.materials.get(name)

    if mat is None:
        mat = bpy.data.materials.new(name=name)
        mat.use_nodes = True                                       # Enables node-based shader
        bsdf = mat.node_tree.nodes.get("Principled BSDF")          # Gets the Principled BSDF node, default shader node for new materials
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)  # (*rgb,1.0) converts (r,g,b) to (r,g,b,a)
    return mat

def create_uv_sphere(name:str = "sphere", radius:float=0.1, location:tuple[float, float, float] = (0.0, 0.0, 0.0))->bpy.data.objects:
    """
    Create a UV sphere primitive (representing an atom/molecule/bead) with the specified name, radius, location, and material.
    ----------
    Args:
        name (str): The name of the sphere object to create.
        radius (float): The radius of the UV sphere.
        location (tuple[float, float, float]): A tuple of (x, y, z) coordinates for the sphere's location.
        material (bpy.data.materials): The material to assign to the sphere.
    Returns:
        bpy.data.objects: The created UV sphere object.
    """

    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, location=location)
    sphere = bpy.context.active_object
    sphere.name = name

    # if sphere.data.materials:
    #     sphere.data.materials[0] = material  # Assign material to existing slot
    # else:
    #     sphere.data.materials.append(material)  # Add new material slot and assign
    return sphere


# ------------------------------------------------------------
# Creating the Import Operator
# ------------------------------------------------------------

# Creating a custom operator for importing LAMMPS atom data from a dump file. 
# This operator will be registered with Blender and will appear in the File > Import menu.
#Operators perform actions like: import, export, mesh operations

class Import_OP_LAMMPS_atoms(Operator, ImportHelper):
    """
    Operator to import LAMMPS atom data from a dump file and create spheres in Blender, respecting per-atom radius..
    """

    bl_idname = "import_scene.lammps_atoms"             # Unique internal identifier for the operator
    bl_label = "Import LAMMPS Atoms with Radius"        # Display name in the UI
    bl_options = {'PRESET', 'UNDO'}                     # Enable supports presets and undo for this operator

    filename_ext = ""                                   # Default file extension for the file selector, Accepts any extension.
    filter_glob: StringProperty(                        # File selector filter to show only relevant files, Max length of 255 characters
        default="*.lammpstrj;*.dump;*.txt;*.atom",
        options={"HIDDEN"},
        maxlen=255,
    ) # pyright: ignore[reportInvalidTypeForm]

    coordinate_scale: FloatProperty(                    # Used because: LAMMPS units are tiny (Ångstrom), Blender units are large (meters),                     
        name="Coordinate scale",                        # so scaling is needed to visualize the atoms at a reasonable size.
        description="Multiply x,y,z by this factor",
        default=8.0,
        min=0.0001,
        max=1e6,
    ) # pyright: ignore[reportInvalidTypeForm]

    radius_scale: FloatProperty(                      
    # To independly control the scaling of the radius values, which may be different from the coordinate scaling.
    # LAMMPS radius values are also typically in Ångstroms, so they also need to be scaled up for Blender.
        name="Radius scale",                            # so scaling is needed to visualize the atoms at a reasonable size.
        description="Multiply radius by this factor",
        default=1.0,
        min=0.0001,
        max=1e6,
    ) # pyright: ignore[reportInvalidTypeForm]

    sphere_radius: FloatProperty(                       # Default radius for spheres if not specified in the dump file,
        name="Default Sphere Radius",
        description="Default radius for spheres if not specified in the dump file",
        default=0.1,
        min=0.000001,
        max=1000.0,
    )# pyright: ignore[reportInvalidTypeForm]

    def execute(self, context):
        """
        Main execution method called when the operator is run. It reads the selected LAMMPS dump file, parses atom data, and creates spheres in Blender.
        Runs when user clicks Import.
        ----------
        Args:
            context (bpy.context): The current context in which the operator is executed.
        """
        file_path = Path(self.filepath)                 # Using pathlib for modern file handling
        if not file_path.is_file():
            self.report({'ERROR'}, f"Can not open file or file not found: {file_path}")
            return {'CANCELLED'}
        
        proto_radius = self.sphere_radius
        proto = create_uv_sphere("_lammps_proto_",  radius=proto_radius)   # Create a prototype sphere to copy for each atom, 
        proto.name = "_lammps_proto_"                   # this is more efficient than creating a new sphere from scratch for each atom.
        #proto.hide_viewport = True                     # Hide the prototype sphere in the viewport, it will serve as a template for duplication.
        #proto.hide_render = True                       # Hide the prototype sphere in renders, it is only used for duplication.

        atoms_data = []                                      # List to store atom data parsed from the file
        types = set()                                   # Set to track unique atom types for material creation
        materials = {}                                  # Dictionary to store materials for each atom type

        current_key = None
        
        with file_path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()                     # Remove leading/trailing whitespace from the line
                if line.startswith("ITEM:"):
                    part = line.split()                 # Get the section type (e.g., "ATOMS", "TIMESTEP", etc.)
                    current_key = part[1] if len(part) > 1 else None  # Set current key to the section type for parsing
                    headers = line.split()[2:]          # Extract column headers after "ITEM:"
                    continue

                if current_key == "ATOMS":              # Only process atoms lines
                    columns = line.split()              # Split the line into components (atom_id, type, x, y, z, radius)
                    if len(columns) < 5:                # Ensure there are at least 5 components (id, type, x, y, z)
                        continue
                    atom_type = columns[1]              # Get the atom type (second column)
                    
                    x, y, z = (
                                float(v) * self.coordinate_scale
                                for v in columns[2:5]
                            )                           # Scale the coordinates by the user-defined scale factor
                                       
                    radius_val = (
                        float(columns[5]) * self.radius_scale * self.coordinate_scale 
                        if len(columns) >= 6
                        else self.sphere_radius
                    )                                   # Get the radius from the file if available, otherwise use the default radius and scale it.

                    atoms_data.append((columns[0],      # stores the whole line of data after scaling
                                       atom_type,
                                       x, y, z, 
                                       radius_val))
                    
                    types.add(atom_type)                # Track unique atom types for material creation

                    
                    if atom_type not in materials:          # Only generate a material if we haven't already for this atom type
                        # Generate a random color for the material (we can customize this as needed)
                        color = (random.random(), random.random(), random.random())
                        materials[atom_type] = ensure_material(f"Type_{atom_type}", color)  # Ensure a material exists for this atom type

        # If no atom data was found, report a warning and clean up the prototype sphere before exiting.
        if not atoms_data: 
            self.report({"WARNING"}, "No atoms found – aborting.")
            bpy.data.objects.remove(proto, do_unlink=True)  
            # Do unlink means it will be removed from the scene and deleted from memory if no other references exist.
            return {"CANCELLED"}
        
        collection = context.collection             # Get the current collection to link new objects to it.
        imported: list[bpy.types.Object] = []       # List to keep track of imported spheres for potential cleanup if needed.
        type_counts: dict[str, int] = {}            # Track running count per atom type for naming
        
        type_collections: dict[str, bpy.types.Collection] = {} # Create a child collection for each atom type under the current collection
        for atom_type in sorted(types):                        # Sort types for consistent ordering in the outliner
            coll_name = f"Type_{atom_type}"
            coll = bpy.data.collections.get(coll_name)
            if coll is None:
                coll = bpy.data.collections.new(coll_name)
            # Ensure it is linked under the active/context collection
            if coll.name not in {c.name for c in collection.children}:
                collection.children.link(coll)
            type_collections[atom_type] = coll

        for (atom_id, atom_type, x, y, z, r) in atoms_data:  # Iterate over the parsed atom data and create spheres for each atom

            atom = proto.copy()                     # Create a copy of the prototype sphere for this atom
            atom.data = proto.data.copy()           # Ensure the mesh data is also copied for independent manipulation

            type_counts[atom_type] = type_counts.get(atom_type,0) + 1       # Increment the count for this atom type
            atom.name = f"{atom_type}_{type_counts[atom_type]}__{atom_id}"  # Name the sphere based on its type and count and atom_ID(e.g., "Type_1_3_67" for the 3rd atom of type 1 with atom ID 67)

            atom.location = (x, y, z)               # Set the location of the sphere based on the atom's coordinates

            scale_factor = (r / proto_radius)       # Calculate the scale factor based on the radius value (adjusted by the prototype radius)
            atom.scale = (scale_factor, scale_factor, scale_factor)  # Scale the sphere uniformly in all dimensions

            
            type_collections[atom_type].objects.link(atom) # Link atom to its atom-type collection
            # collection.objects.link(atom)                  # Link the new sphere to the current collection in Blender

            material = materials.get(atom_type)             # Get the material for this atom type
            if material:
                if atom.data.materials:
                    atom.data.materials[0] = material       # Assign material to existing slot
                else:
                    atom.data.materials.append(material)    # Add new material slot and assign

            imported.append(atom)                           # Keep track of imported spheres

        bpy.data.objects.remove(proto, do_unlink=True)  # Clean up the prototype sphere after importing all atoms
        bpy.ops.object.select_all(action='DESELECT')    # Deselect all objects to leave the imported spheres selected for user convenience

        for obj in imported:
            obj.select_set(True)                        # Select the imported spheres for user convenience
        
        context.view_layer.objects.active = imported[0]  # Set the first imported sphere as the active object for user convenience
        self.report({"INFO"}, f"Imported {len(imported)} atoms as spheres.")
        return {"FINISHED"}

# ------------------------------------------------------------
# Menu Integration
# ------------------------------------------------------------

def menu_func_import(self, context):
    """
    Function to add the import operator to the File > Import menu in Blender.
    ----------
    Args:
        self: The menu object to which the operator will be added.
        context: The current context in which the menu is being drawn.
    """
    self.layout.operator(
        Import_OP_LAMMPS_atoms.bl_idname, 
        text="LAMMPS Atoms (.lammpstrj, .dump)"
        )

# ------------------------------------------------------------
# Register
# ------------------------------------------------------------

classes = ( # List of classes to register with Blender, currently only the import operator.
    Import_OP_LAMMPS_atoms,
)

def register():

    for cls in classes:
        bpy.utils.register_class(cls)                         # Register each class with Blender
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)  # Add the import operator to the File > Import menu

def unregister():                                             # Unregister the classes and remove the menu item when the add-on is disabled or uninstalled.

    for cls in classes:
        bpy.utils.unregister_class(cls)

    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)


if __name__ == "__main__":                                   # Allows running script directly.
    register()

### Personla notes on context:

# In Blender scripting context is extremely important. It tells Blender what part of the UI/scene the operator is currently acting on.
# It its essence context is a snapshot of Blender’s current state. It contains references to things like:
# 1. current scene
# 2. active object
# 3. selected objects
# 4. current collection
# 5. current view layer
# 6. UI area
# 7. window

# In operators:
# def execute(self, context):
# Blender automatically passes the current context.

















        # with file_path.open('r') as f:
        #     for line in f:
        #         line = line.strip()
        #         if line.startswith("ITEM: ATOMS"):
        #             current_key = "ATOMS"
        #             headers = line.split()[2:]  # Extract column headers after "ITEM: ATOMS"
        #             continue
        #         elif line.startswith("ITEM:"):
        #             current_key = None  # Reset current key when a new section starts
        #             continue

        #         if current_key == "ATOMS":
        #             values = line.split()
        #             atom_data = dict(zip(headers, values))  # Create a dictionary of atom data using headers as keys
        #             atoms.append(atom_data)  # Add the atom data to the list
        #             types.add(atom_data['type'])  # Track unique atom types for material creation
        # # Create materials for each unique atom type
        # for atom_type in types:
        #     # Generate a random color for the material (you can customize this as needed)
        #     color = (random.random(), random.random(), random.random())
        #     materials[atom_type] = ensure_material(f"Type_{atom_type}", color)  # Ensure a material exists for this atom type
        # # Create spheres for each atom using the prototype and assign materials
        # for atom in atoms_data:
        #     radius, atom_type, x, y, z, radius_val = atom
        #     material = materials.get(atom_type)  # Get the material for this atom type
        #     sphere = proto.copy()                # Create a copy of the prototype sphere
        #     sphere.data = proto.data.copy()      # Ensure the mesh data is also copied for independent manipulation
        #     sphere.location = (x, y, z)          # Set the location of the sphere based on the atom's coordinates
        #     sphere.scale = (radius_val / proto_radius,) * 3  # Scale the sphere based on the radius value (adjusted by the prototype radius)
        #     bpy.context.collection.objects.link(sphere)  # Link the new sphere to the current collection in Blender
        #     if material:
        #         if sphere.data.materials:
        #             sphere.data.materials[0] = material  # Assign material to existing slot
        #         else:
        #             sphere.data.materials.append(material)  # Add new material slot and assign
