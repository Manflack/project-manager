import os
import json
import tkinter as tk
from tkinter import ttk, scrolledtext
import subprocess
import threading
import docker

class PersistentHashMap:
    def __init__(self, filename='data.json'):
        self.filename = filename
        self.data = self._load_data()

    def _load_data(self):
        if os.path.exists(self.filename):
            with open(self.filename, 'r') as file:
                try:
                    return json.load(file)
                except json.JSONDecodeError:
                    return {}
        return {}

    def _save_data(self):
        with open(self.filename, 'w') as file:
            json.dump(self.data, file, indent=4)

    def set(self, key, value):
        self.data[key] = value
        self._save_data()

    def get(self, key):
        return self.data.get(key, None)
    
    def get_or_default(self, key, default):
        if key not in self.data:
            self.set(key, default)
        return self.data.get(key, default)
    
    def add_dict(self, key, sub_key, sub_value):
        if key not in self.data:
            self.data[key] = {}
        self.data[key][sub_key] = sub_value
        self._save_data()

    def remove_dict(self, key, sub_key):
        if key in self.data and sub_key in self.data[key]:
            del self.data[key][sub_key]
            self._save_data()

mapVariables = PersistentHashMap()
BASE_PATH = mapVariables.get_or_default('projects_dir', os.path.dirname(os.path.abspath(__file__)))
client = docker.from_env()

def create_dockerfile(project_name, env_vars):
    env_vars_str = '\n'.join([f'ENV {key}="{value}"' for key, value in env_vars.items()])
    dockerfile_content = f"""
FROM openjdk:11-jre-slim
WORKDIR /app
COPY target/*.jar app.jar
{env_vars_str}
EXPOSE {env_vars.get('server.port', '8080')}
CMD ["java", "-Xms256m", "-Xmx512m", "-Djava.awt.headless=false", "-jar", "app.jar"]
"""
    dockerfile_path = os.path.join(BASE_PATH, project_name, 'Dockerfile')
    with open(dockerfile_path, 'w') as f:
        f.write(dockerfile_content)
    return dockerfile_path

def build_docker_image(project_name, log_widget):
    dockerfile_path = os.path.join(BASE_PATH, project_name)
    try:
        image, logs = client.images.build(path=dockerfile_path, tag=f"{project_name.lower()}:latest", rm=True)
        for log in logs:
            if 'stream' in log:
                log_widget.insert(tk.END, log['stream'])
                log_widget.yview(tk.END)
        return True
    except docker.errors.BuildError as e:
        log_widget.insert(tk.END, f"Error al construir la imagen de Docker: {str(e)}\n")
        return False


def stream_logs(container, log_widget):
    for log in container.logs(stream=True, follow=True):
        log_widget.insert(tk.END, log.decode('utf-8'))
        log_widget.yview(tk.END)

import subprocess

def build_java_project(project_name, log_widget):
    project_path = os.path.join(BASE_PATH, project_name)
    try:
        log_widget.insert(tk.END, f"Construyendo proyecto Java: {project_name}\n")
        result = subprocess.run(['mvn', 'clean', 'package', '-DskipTests'], 
                                cwd=project_path, 
                                capture_output=True, 
                                text=True)
        log_widget.insert(tk.END, result.stdout)
        log_widget.insert(tk.END, result.stderr)
        if result.returncode != 0:
            log_widget.insert(tk.END, "Error al construir el proyecto Java\n")
            return False
        log_widget.insert(tk.END, "Proyecto Java construido exitosamente\n")
        return True
    except Exception as e:
        log_widget.insert(tk.END, f"Error al construir el proyecto Java: {str(e)}\n")
        return False

def start_project(project_name, log_widget):
    default_vars = mapVariables.get('default_env_vars') or {}
    project_vars = mapVariables.get_or_default(project_name, {})
    env_vars = {**default_vars, **project_vars}
    
    if not build_java_project(project_name, log_widget):
        return
    
    dockerfile_path = create_dockerfile(project_name, env_vars)
    
    build_docker_image(project_name, log_widget)
    
    try:
        container = client.containers.run(
            f"{project_name.lower()}:latest",
            name=project_name,
            detach=True,
            network="docker_network",
            ports={f"{env_vars.get('server.port', '8080')}/tcp": env_vars.get('server.port', '8080')},
            publish_all_ports=True,
            remove=True
        )
        mapVariables.add_dict('CONTAINERS', project_name, container.id)
        log_widget.insert(tk.END, f"Contenedor {project_name} iniciado\n")
        
        threading.Thread(target=stream_logs, args=(container, log_widget), daemon=True).start()
        
        def check_container_status():
            container.reload()
            if container.status == 'exited':
                log_widget.insert(tk.END, f"Contenedor {project_name} se ha detenido. Código de salida: {container.attrs['State']['ExitCode']}\n")
                mapVariables.remove_dict('CONTAINERS', project_name)
            else:
                root.after(500, check_container_status)  # Verificar cada 5 segundos
        
        root.after(500, check_container_status)
        
    except docker.errors.APIError as e:
        log_widget.insert(tk.END, f"Error al iniciar el contenedor: {str(e)}\n")

def stop_project(project_name, log_widget):
    containers = mapVariables.get('CONTAINERS') or {}
    if project_name in containers:
        container_id = containers[project_name]
        try:
            container = client.containers.get(container_id)
            container.stop()
            log_widget.insert(tk.END, f"Contenedor {project_name} detenido\n")
        except docker.errors.NotFound:
            log_widget.insert(tk.END, f"Contenedor {project_name} no encontrado\n")
        except docker.errors.APIError as e:
            log_widget.insert(tk.END, f"Error al detener el contenedor: {str(e)}\n")
        mapVariables.remove_dict('CONTAINERS', project_name)

def restart_project(project_name, log_widget):
    stop_project(project_name, log_widget)
    start_project(project_name, log_widget)

def detect_projects():
    projects = [name for name in os.listdir(BASE_PATH) if os.path.isdir(os.path.join(BASE_PATH, name))]
    return projects

root = tk.Tk()
root.title("Gestor de Proyectos")

main_frame = ttk.Frame(root)
main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

projects_frame = ttk.Frame(main_frame)
projects_frame.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)

details_frame = ttk.Frame(main_frame)
details_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=10, pady=10)

project_list = tk.Listbox(projects_frame, exportselection=False)
project_list.pack(fill=tk.Y, expand=True)

projects = detect_projects()
for project in projects:
    project_list.insert(tk.END, project)

variables_frame = ttk.Frame(details_frame)
variables_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

common_vars_frame = ttk.LabelFrame(variables_frame, text="Variables Comunes")
common_vars_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)

common_vars_text = scrolledtext.ScrolledText(common_vars_frame, height=10)
common_vars_text.pack(fill=tk.BOTH, expand=True)

project_vars_frame = ttk.LabelFrame(variables_frame, text="Variables de Entorno")
project_vars_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=10, pady=10)

env_vars_text = scrolledtext.ScrolledText(project_vars_frame, height=10)
env_vars_text.pack(fill=tk.BOTH, expand=True)

log_frame = ttk.LabelFrame(details_frame, text="Logs")
log_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=10)

log_texts = {}
for project in projects:
    log_texts[project] = scrolledtext.ScrolledText(log_frame, height=10)
    log_texts[project].pack(fill=tk.BOTH, expand=True)

def load_project_details(event):
    if not project_list.curselection():
        return
    selected_project = project_list.get(project_list.curselection())
    
    common_vars_text.delete(1.0, tk.END)
    default_vars = mapVariables.get('default_env_vars')
    if default_vars:
        for key, value in default_vars.items():
            common_vars_text.insert(tk.END, f'{key}={value}\n')
    
    env_vars_text.delete(1.0, tk.END)
    project_env_vars = mapVariables.get(selected_project)
    if project_env_vars:
        for key, value in project_env_vars.items():
            env_vars_text.insert(tk.END, f'{key}={value}\n')

    for text_widget in log_texts.values():
        text_widget.pack_forget()
    log_texts[selected_project].pack(fill=tk.BOTH, expand=True)

if projects:
    project_list.selection_set(0)
    load_project_details(None)

project_list.bind("<<ListboxSelect>>", load_project_details)

def save_common_env(event=None):
    default_env_vars = {}
    common_vars_text_content = common_vars_text.get(1.0, tk.END).strip().split('\n')
    for line in common_vars_text_content:
        if '=' in line:
            key, value = line.strip().split('=', 1)
            default_env_vars[key] = value
    mapVariables.set('default_env_vars', default_env_vars)

def save_project_env(event=None):
    if not project_list.curselection():
        return
    selected_project = project_list.get(project_list.curselection())
    env_vars = {}
    env_vars_text_content = env_vars_text.get(1.0, tk.END).strip().split('\n')
    for line in env_vars_text_content:
        if '=' in line:
            key, value = line.strip().split('=', 1)
            env_vars[key] = value
    mapVariables.set(selected_project, env_vars)

'''
def stop_all_processes(log_widget):
    containers = mapVariables.get('CONTAINERS') or {}
    for project_name, container_id in containers.items():
        try:
            container = client.containers.get(container_id)
            container.stop()
            log_widget.insert(tk.END, f"Contenedor {project_name} detenido\n")
        except docker.errors.NotFound:
            log_widget.insert(tk.END, f"Contenedor {project_name} no encontrado\n")
        except docker.errors.APIError as e:
            log_widget.insert(tk.END, f"Error al detener el contenedor {project_name}: {str(e)}\n")
    mapVariables.set('CONTAINERS', {})
'''

common_vars_text.bind("<FocusOut>", save_common_env)
env_vars_text.bind("<FocusOut>", save_project_env)

buttons_frame = ttk.Frame(details_frame)
buttons_frame.pack(side=tk.BOTTOM, fill=tk.X)

ttk.Button(buttons_frame, text="Iniciar", command=lambda: start_project(project_list.get(tk.ACTIVE), log_texts[project_list.get(tk.ACTIVE)])).pack(side=tk.LEFT, padx=5, pady=5)
ttk.Button(buttons_frame, text="Detener", command=lambda: stop_project(project_list.get(tk.ACTIVE), log_texts[project_list.get(tk.ACTIVE)])).pack(side=tk.LEFT, padx=5, pady=5)
ttk.Button(buttons_frame, text="Reiniciar", command=lambda: restart_project(project_list.get(tk.ACTIVE), log_texts[project_list.get(tk.ACTIVE)])).pack(side=tk.LEFT, padx=5, pady=5)
ttk.Button(buttons_frame, text="Guardar Variables", command=save_project_env).pack(side=tk.LEFT, padx=5, pady=5)

#stop_all_processes(log_texts[project_list.get(tk.ACTIVE)])

root.mainloop()
