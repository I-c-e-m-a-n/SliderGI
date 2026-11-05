"""Handwritten Pygame clock components for deterministic integration tests."""
REQUEST = ('Generate a Python Pygame clock with accurate animated hour, minute and second hands, '
           'notches at every hour, analog/digital switching with Space, and colour changes with C. Do not use tkinter.')
SOURCES = {
 'hand_angles': '''def hand_angles(now):
    local = time.localtime(now)
    seconds = local.tm_sec + now % 1
    minutes = local.tm_min + seconds / 60
    hours = local.tm_hour % 12 + minutes / 60
    return hours * math.pi / 6, minutes * math.pi / 30, seconds * math.pi / 30
''',
 'handle_event': '''def handle_event(event, state):
    if event.type == pygame.QUIT:
        state['running'] = False
    elif event.type == pygame.KEYDOWN:
        if event.key == pygame.K_SPACE:
            state['digital'] = not state['digital']
        elif event.key == pygame.K_c:
            state['colour'] = (state['colour'] + 1) % len(COLOURS)
''',
 'draw_analog': '''def draw_analog(surface, now, colour):
    centre = (WIDTH // 2, HEIGHT // 2)
    for hour in range(12):
        angle = hour * math.pi / 6
        a = (centre[0] + 160 * math.sin(angle), centre[1] - 160 * math.cos(angle))
        b = (centre[0] + 175 * math.sin(angle), centre[1] - 175 * math.cos(angle))
        pygame.draw.line(surface, colour, a, b, 3)
    for angle, length, width in zip(hand_angles(now), (90, 130, 150), (7, 4, 2)):
        end = (centre[0] + length * math.sin(angle), centre[1] - length * math.cos(angle))
        pygame.draw.line(surface, colour, centre, end, width)
''',
 'draw_digital': '''def draw_digital(surface, now, colour):
    label = time.strftime('%H:%M:%S', time.localtime(now))
    image = pygame.font.Font(None, 64).render(label, True, colour)
    surface.blit(image, image.get_rect(center=(WIDTH // 2, HEIGHT // 2)))
''',
 'main': '''def main():
    pygame.init()
    surface = pygame.display.set_mode((WIDTH, HEIGHT))
    timer = pygame.time.Clock()
    state = {'running': True, 'digital': False, 'colour': 0}
    while state['running']:
        for event in pygame.event.get():
            handle_event(event, state)
        now = time.time()
        surface.fill((0, 0, 0))
        colour = COLOURS[state['colour']]
        if state['digital']:
            draw_digital(surface, now, colour)
        else:
            draw_analog(surface, now, colour)
        pygame.display.flip()
        timer.tick(60)
    pygame.quit()
'''}


def plan():
    names = ['main', 'draw_analog', 'draw_digital', 'handle_event', 'hand_angles']
    deps = {'main': ['draw_analog', 'draw_digital', 'handle_event'], 'draw_analog': ['hand_angles'],
            'draw_digital': [], 'handle_event': [], 'hand_angles': []}
    used = {'hand_angles': ['math', 'time'], 'handle_event': ['pygame', 'COLOURS'],
            'draw_analog': ['math', 'pygame', 'WIDTH', 'HEIGHT'],
            'draw_digital': ['time', 'pygame', 'WIDTH', 'HEIGHT'],
            'main': ['pygame', 'time', 'WIDTH', 'HEIGHT', 'COLOURS']}
    return {'architecture': 'One Pygame event loop with shared state and separate rendering/event functions.',
        'requirements': [
            {'id': 'R1', 'text': 'Accurate animated three-hand clock and twelve hour notches', 'components': ['draw_analog', 'main']},
            {'id': 'R2', 'text': 'Space switches analog and digital display', 'components': ['handle_event', 'draw_digital', 'main']},
            {'id': 'R3', 'text': 'C changes colour used by both displays', 'components': ['handle_event', 'main']},
            {'id': 'R4', 'text': 'Do not use tkinter', 'components': ['main']}],
        'imports': ['import math', 'import time', 'import pygame'],
        'dependencies': [{'module': 'pygame', 'package': 'pygame'}],
        'variables': [{'name': 'WIDTH', 'value': '640'}, {'name': 'HEIGHT', 'value': '480'},
                      {'name': 'COLOURS', 'value': '[(255, 255, 255), (255, 120, 40), (80, 200, 255)]'}],
        'classes': [], 'entrypoint': 'main',
        'components': [{'name': name, 'signature': SOURCES[name].splitlines()[0][:-1],
                        'description': {'main': 'Run event/update/render loop at 60 FPS using current wall-clock time.',
                            'draw_analog': 'Draw 12 hour notches and continuously animated hour/minute/second hands.',
                            'draw_digital': 'Render current time digitally with selected colour.',
                            'handle_event': 'Handle quit, Space toggling and C colour cycling.',
                            'hand_angles': 'Compute continuous angles from wall-clock seconds.'}[name],
                        'depends_on': deps[name], 'uses': used[name], 'requirements': ['R1', 'R2', 'R3', 'R4']} for name in names]}


PASS = {'verdict': 'pass', 'evidence': 'Event loop pumps controls, reads updated state, redraws at current time and flips display.',
        'evidence_snippets': ['pygame.display.flip()'], 'issues': [], 'missing_requirements': []}
