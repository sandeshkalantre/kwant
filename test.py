import matplotlib.pyplot
import kwant

def make_triangle():
    syst = kwant.Builder()
    lat = kwant.lattice.triangular()
    sites = [lat(0, 0), lat(1, 0), lat(0, 1)]
    n = len(sites)
    syst[sites] = None
    for i in range(n):
        syst[sites[i], sites[(i + 1) % n]] = None
    return syst.finalized()


def triangle():
    syst = make_triangle()

    current = []
    c = sum(s.pos for s in syst.sites) / len(syst.sites)
    for i, j in syst.graph:
        a = syst.sites[i].pos
        b = syst.sites[j].pos
        ca = a - c
        ab = b - a
        current.append(ca[0] * ab[1] - ca[1] * ab[0])

    R, Z = kwant.plotter.interpolate_current(syst, current, sigma=0.1)
    kwant.plotter.plot_current_density(R, Z)


def inside_disk(r):
    def check(pos):
        x, y = pos
        return x*x + y*y < rr
    rr = r*r
    return check


if __name__ == '__main__':
    triangle()
