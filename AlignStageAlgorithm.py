

import numpy
import math
from scipy.optimize import fsolve
from scipy.optimize import least_squares

def eulerYXZ(r):

    a1, a2, a3 = r[0], r[1], r[2]

    # Rotation matrix around X-axis
    R1 = numpy.array([
        [1, 0, 0],
        [0, numpy.cos(a1), -numpy.sin(a1)],
        [0, numpy.sin(a1), numpy.cos(a1)]
    ])

    # Rotation matrix around Y-axis
    R2 = numpy.array([
        [numpy.cos(a2), 0, numpy.sin(a2)],
        [0, 1, 0],
        [-numpy.sin(a2), 0, numpy.cos(a2)]
    ])

    # Rotation matrix around Z-axis
    R3 = numpy.array([
        [numpy.cos(a3), -numpy.sin(a3), 0],
        [numpy.sin(a3), numpy.cos(a3), 0],
        [0, 0, 1]
    ])

    # Compute the final rotation matrix
    Rm = R2 @ R1 @ R3

    return Rm

def CalculateBallPosition(r, t, v0, b0, m, m0, a0, c0):
    v10, v20, v30 = v0[:3], v0[3:6], v0[6:9]
    b10, b20, b30 = b0[:3], b0[3:6], b0[6:9]
    m1, m2, m3 = m[:3], m[3:6], m[6:9]
    m10, m20, m30 = m0[:3], m0[3:6], m0[6:9]
    a10, a20, a30 = a0[:3], a0[3:6], a0[6:9]
    p10, p20, p30 = c0[:3], c0[3:6], c0[6:9]

    R = eulerYXZ(r)

    # Vertex rotation
    v1 = t + R @ v10
    v2 = t + R @ v20
    v3 = t + R @ v30

    # Slave axis rotation
    a1 = R @ a10
    a2 = R @ a20
    a3 = R @ a30
    p1 = t + R @ p10
    p2 = t + R @ p20
    p3 = t + R @ p30

    # Intersection of slave axis and ball plane
    k1 = numpy.dot((m10 - p1), m1) / numpy.dot(a1, m1)
    k2 = numpy.dot((m20 - p2), m2) / numpy.dot(a2, m2)
    k3 = numpy.dot((m30 - p3), m3) / numpy.dot(a3, m3)

    b1 = p1 + k1 * a1
    b2 = p2 + k2 * a2
    b3 = p3 + k3 * a3

    #b = numpy.hstack([b1, b2, b3])
    b = numpy.concatenate((b1, b2, b3))

    return b

# ����ȭ�� �Լ� ����
def ResidualFun(x, v0, b0, m, m0, a0, c0, b):
    r = x[:3]
    t = x[3:6]
    # CalculateBallPosition �Լ� ȣ��
    estimated_b = CalculateBallPosition(r, t, v0, b0, m, m0, a0, c0)
    
    return b-estimated_b


def CalculateAttitude(v0, b0, m, m0, a0, c0, b, x0):

    result = least_squares(ResidualFun, x0, args=(v0, b0, m, m0, a0, c0, b), method='trf')

    return result.x



def CalculateBallPositionPivot(r, ti, v0, b0, m, m0, a0, c0, p):
    v10, v20, v30 = v0[:3], v0[3:6], v0[6:9]
    b10, b20, b30 = b0[:3], b0[3:6], b0[6:9]
    m1, m2, m3 = m[:3], m[3:6], m[6:9]
    m10, m20, m30 = m0[:3], m0[3:6], m0[6:9]
    a10, a20, a30 = a0[:3], a0[3:6], a0[6:9]
    p10, p20, p30 = c0[:3], c0[3:6], c0[6:9]

    R = eulerYXZ(r)
    t = ti + p -R @ p

    # Vertex rotation
    v1 = t + R @ v10
    v2 = t + R @ v20
    v3 = t + R @ v30

    # Slave axis rotation
    a1 = R @ a10
    a2 = R @ a20
    a3 = R @ a30
    p1 = t + R @ p10
    p2 = t + R @ p20
    p3 = t + R @ p30

    # Intersection of slave axis and ball plane
    k1 = numpy.dot((m10 - p1), m1) / numpy.dot(a1, m1)
    k2 = numpy.dot((m20 - p2), m2) / numpy.dot(a2, m2)
    k3 = numpy.dot((m30 - p3), m3) / numpy.dot(a3, m3)

    b1 = p1 + k1 * a1
    b2 = p2 + k2 * a2
    b3 = p3 + k3 * a3

    #b = numpy.hstack([b1, b2, b3])
    b = numpy.concatenate((b1, b2, b3))

    return b

# ����ȭ�� �Լ� ����
def ResidualFunPivot(x, v0, b0, m, m0, a0, c0, b, p):
    r = x[:3]
    ti = x[3:6]
    # CalculateBallPosition �Լ� ȣ��
    estimated_b = CalculateBallPositionPivot(r, ti, v0, b0, m, m0, a0, c0, p)
    
    return b-estimated_b


def CalculateAttitudePivot(v0, b0, m, m0, a0, c0, b, x0, p):

    result = least_squares(ResidualFunPivot, x0, args=(v0, b0, m, m0, a0, c0, b, p), method='trf')

    #x =[r ti]
    return result.x

