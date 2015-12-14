# Authors: A. Ditter, O. Hoidn, and R. Valenza

import os
import numpy as np
import numpy.ma as ma
import matplotlib.pyplot as plt
import itertools
import scipy
from scipy.ndimage.filters import gaussian_filter
from scipy.interpolate import griddata
import scipy.interpolate as interpolate
#from scipy.interpolate import interp2d
from functools import partial
from collections import namedtuple
import ipdb

import config
from dataccess import data_access as data
from dataccess import utils

# default powder peak width, in degrees
DEFAULT_PEAK_WIDTH = 1.5

# hbar c in eV * Angstrom
HBARC = 1973. 

global verbose
verbose = True

# TODO
Dataset = namedtuple('Dataset', ['dataref', 'ref_type', 'detid', 'compound_list'])

def get_detid_parameters(detid):
    """
    Given a detector ID, extract its detector geometry parameters from
    config.py and return them.
    """
    paramdict = config.detinfo_map[detid].geometry
    (phi, x0, y0, alpha, r) = paramdict['phi'], paramdict['x0'],\
        paramdict['y0'], paramdict['alpha'], paramdict['r']
    return (phi, x0, y0, alpha, r)


# TODO: add an hdf5 option to data_extractor
#import h5py
#hdf5folder = '/reg/d/psdm/MEC/mecd6714/hdf5/'
#
## GetArray function:
## Gets a quad image as an array from the hdf5 file.
## Pattern of CsPad chips determined from testing.py and old images on 3/30/15.
## Same function as averager4.py
## Input:
##   run: run number
##   event: event number (starts at 1)
## Outputs:
##   numpy array shape 830 x 825
#def getArray(run, event):
#    f=h5py.File(hdf5folder+'mecd6714-r%04.i.h5' %run,'r')
#    quaddata = f['/Configure:0000/Run:0000/CalibCycle:0000/CsPad::ElementV2/MecTargetChamber.0:Cspad.0/data']
#    output = np.zeros((830,825))
#    corners = [
#        [429,421],
#        [430,634],
#        [420,1],
#        [633,0],
#        [0,213],
#        [0,1],
#        [16,424],
#        [228,424]
#        ]
#    rotated = [1,1,0,0,3,3,0,0]
#    for i, arr in enumerate(quaddata[event-1]):
#        a = np.rot90(np.insert(arr,(193,193,193,193),0,axis = 1),rotated[i])
#        if rotated[i]:
#            output[corners[i][0]:corners[i][0]+392, corners[i][1]:corners[i][1]+185] = a
#        else:
#            output[corners[i][0]:corners[i][0]+185, corners[i][1]:corners[i][1]+392] = a
#    return output

def CSPAD_pieces(arr):
    """
    Takes an assembeled quad CSPAD frame and returns a list of
    16 arrays, each containing one of its component chips.
    """
    # TODO: if there's any chance of this module being used with detectors
    # other than quad CSPAD, or if there's variation in the positioning of
    # chips between different quad CSPADs, then these lists need to be 
    # detector-specific parameters in config.py.
    split_x = [0, 196, 825/2, 622, 825]
    split_y = [0, 198, 830/2, 625, 830]
    result = []
    #f, ax = plt.subplots(4, 4)
    def piece(n, m):
        subsec =  arr[split_y[n]: split_y[n + 1], split_x[m]: split_x[m + 1]]
        result.append(subsec)
        #ax[n][m].imshow(subsec)
    [piece(i, j) for i in range(4) for j in range(4)]
    return result


@utils.eager_persist_to_file("cache/xrd.combine_masks/")
def combine_masks(imarray, mask_paths, verbose = False):
    """
    Takes a list of paths to .npy mask files and returns a numpy array
    consisting of those masks ANDed together.
    """
    # Initialize the mask based on zero values in imarray.
    base_mask = ma.make_mask(np.ones(np.shape(imarray)))
    base_mask[imarray == 0.] = False
    if not mask_paths:
        print "No additional masks provided"
        return base_mask
    else:
        # Data arrays must be transposed here for the same reason that they
        # are in data_extractor.
        masks = map(lambda path: np.load(path).T, mask_paths)
        if verbose:
            print "Applying mask(s): ", mask_paths
        return base_mask & reduce(lambda x, y: x & y, masks)

#def data_extractor(path = None, label = None, arr = None, detid = None,
#    apply_mask = True, event_data_getter = None, event_filter = None):
def data_extractor(dataset, apply_mask = True, event_data_getter = None,
    event_filter = None):
    """
    # TODO: update this
    Returns CSPAD image data in the correct format for all other functions in
    this module given a data path, run group label, or raw array. Optionally
    masks out pixels using mask files specified in config.extra_masks.

    Parameters
    ---------
    path : str
        path to an ASCII datafile
    label : str
        run group label specified from the logbook
    arr : numpy.ndarray
        2d array of CSPAD data
    detid : hashable type
        detector id, one of the keys of config.extra_masks
    apply_mask : boolean
        if detid is provided, mask the ret
    """
    # TODO: improve the handling of different data type references
    # Transpose (relative to the shape of the array returned by psana is
    # necessary due to choice of geometry definition in this module.
    # TODO: improve the handling of different types of data references
    if dataset.ref_type == 'array':
        if not isinstance(dataset.dataref, np.ndarray):
            raise ValueError("ref_type inconsistent with type of dataref")
        imarray, event_data =  dataset.dataref.T, None

    elif dataset.ref_type == 'path':
        imarray, event_data =  np.genfromtxt(dataset.dataref).T, None

    elif dataset.ref_type == 'label':
        imarray, event_data =  data.get_label_data(dataset.dataref, dataset.detid,
            event_data_getter = event_data_getter)
        imarray = imarray.T

    else:
        raise ValueError("Invalid argument combination. Data source must be specified by detid and either path or label")
    if apply_mask:
        extra_masks = config.detinfo_map[dataset.detid].extra_masks
        combined_mask = combine_masks(imarray, extra_masks)
        imarray *= combined_mask
    return imarray, event_data

def get_x_y(imarray, phi, x0, y0, alpha, r):
    """
    Given CSPAD geometry parameters and an assembeled image data array, return
    two arrays (each of the same shape as the image data) with values replaced
    by row/column indices.
    """
    length, width = imarray.shape
    y = np.vstack(np.ones(width)*i for i in range(length))
    ecks = np.vstack([1 for i in range(length)])
    x = np.hstack(ecks*i for i in range(width))
    return x, y

def get_beta_rho(imarray, phi, x0, y0, alpha, r):
    """
    Given CSPAD geometry parameters and an assembeled image data array, return
    (1) an array (of the same shape as the image data) with 2theta scattering
    angle values and (2) an array (of the same shape as the image data) with
    rho (distance) values.
    """
    x, y = get_x_y(imarray, phi, x0, y0, alpha, r)
    try:
        x2 = -np.cos(phi) *(x-x0) + np.sin(phi) * (y-y0)
        y2 = -np.sin(phi) * (x-x0) - np.cos(phi) * (y-y0)
    except AttributeError:
        raise AttributeError("Missing geometry data in config.py")
    rho = (r**2 + x2**2 + y2**2)**0.5
    y1 = y2 * np.cos(alpha) + r * np.sin(alpha)
    z1 = - y2 * np.sin(alpha) + r * np.cos(alpha)
    
    # beta is the twotheta value for a given (x,y)
    beta = np.arctan2((y1**2 + x2**2)**0.5, z1) * 180 / np.pi
    return beta, rho
    

# translate(phi, x0, y0, alpha, r)
# Produces I vs theta values for imarray. For older versions, see bojangles_old.py
# Inputs:  detector configuration parameters and diffraction image
# Outputs:  lists of intensity and 2theta values (data)
def translate(phi, x0, y0, alpha, r, imarray, fiducial_ellipses = None):
    # fiducial ellipse width
    ew = .1
    # beta is the twotheta value for a given (x,y)
    beta, rho = get_beta_rho(imarray, phi, x0, y0, alpha, r)
    if fiducial_ellipses is not None:
        fiducial_value = np.max(np.nan_to_num(imarray))/5.
        for ang in fiducial_ellipses:
            imarray = np.where(np.logical_and(beta > ang - ew, beta < ang + ew), fiducial_value, imarray)
    imarray = imarray * np.square(rho)
    
    newpoints = np.vstack((beta.flatten(), imarray.flatten()))
    
    return newpoints.T, imarray


def binData(mi, ma, stepsize, valenza = True):
    """
    Input:  a minimum, a maximum, and a stepsize
    Output:  a list of bins
    """
    if verbose: print "creating angle bins"
    binangles = list()
    binangles.append(mi)
    i = mi
    while i < ma-(stepsize/2):
        i += stepsize
        binangles.append(i)

    return binangles


@utils.eager_persist_to_file("cache/xrd.process_imarray/")
def process_imarray(detid, imarray, nbins = 1000, verbose = True, fiducial_ellipses = None, bgsub = True, compound_list = []):
    """
    Given a detector ID and assembeled CSPAD image data array, compute the
    powder pattern.

    Outputs:  data in bins, intensity vs. theta. Saves data to file
    """

    # TODO: make this take dataset as an argument
    # Manually entered data after 2015/04/01 calibration. (really)
    # See Testing.nb for details.
    # Coordinates in pixels. 0.011cm per pixel.
    # LD67 inputs
    #(phi, x0, y0, alpha, r) = (0.027763, 322.267, 524.473, 0.787745, 1082.1)
    (phi, x0, y0, alpha, r) = get_detid_parameters(detid)
    if bgsub:
        imarray = subtract_background_full_frame(imarray, detid, compound_list)
    data, imarray = translate(phi, x0, y0, alpha, r, imarray, fiducial_ellipses = fiducial_ellipses)
    
    thetas = data[:,0]
    intens = data[:,1]

    # algorithm for binning the data
    ma = max(thetas)
    mi = min(thetas)
    stepsize = (ma - mi)/(nbins)
    binangles = binData(mi, ma, stepsize)
    numPix = [0] * (nbins+1)
    intenValue = [0] * (nbins+1)
    
    if verbose: print "putting data in bins"        
    # find which bin each theta lies in and add it to count
    for j,theta in enumerate(thetas):
        if intens[j] != 0:
            k = int(np.floor((theta-mi)/stepsize))
            numPix[k]=numPix[k]+1
            intenValue[k]=intenValue[k]+intens[j]
    # form average by dividing total intensity by the number of pixels
    if verbose: print "adjusting intensity"
    adjInten = np.nan_to_num((np.array(intenValue)/np.array(numPix)))
    
#    if np.min(adjInten) < 0:
#        print "WARNING: Negative values have been suppressed in final powder pattern (may indicate background subtraction with an inadequate data mask)."
#        adjInten[adjInten < 0.] = 0.
    return binangles, adjInten, imarray

@utils.eager_persist_to_file("cache/xrd.process_dataset/")
def process_dataset(dataset, nbins = 1000, verbose = True, fiducial_ellipses = None, bgsub = True):
    imarray, event_data = data_extractor(dataset)
    binangles, adjInten, imarray = process_imarray(dataset.detid, imarray,
        fiducial_ellipses = fiducial_ellipses, bgsub = bgsub, compound_list = dataset.compound_list)
    return binangles, adjInten, imarray

def proc_all_datasets(datasets, nbins = 1000, verbose = True, fiducial_ellipses = None, bgsub = True):
    outputs = map(partial(process_dataset, nbins = nbins,
        verbose = verbose, bgsub = bgsub), datasets)
    binangles_list, intensities_list, imarrays = zip(*outputs)
    patterns = map(lambda tup: list(tup), zip(binangles_list, intensities_list))
    return patterns, imarrays


def save_data(angles, intensities, save_path):
    dirname = os.path.dirname(save_path)
    if dirname and (not os.path.exists(dirname)):
        os.system('mkdir -p ' + os.path.dirname(save_path))
    np.savetxt(save_path, [angles, intensities])

def save_image(save_path, imarr):
    dirname = os.path.dirname(save_path)
    if dirname and (not os.path.exists(dirname)):
        os.system('mkdir -p ' + os.path.dirname(save_path))
    scipy.misc.imsave(save_path, imarr)
    




# From: http://stackoverflow.com/questions/7997152/python-3d-polynomial-surface-fit-order-dependent
def polyfit2d(x, y, z, order=3):
    ncols = (order + 1)**2
    G = np.zeros((x.size, ncols))
    ij = itertools.product(range(order+1), range(order+1))
    for k, (i,j) in enumerate(ij):
        G[:,k] = x**i * y**j
    m, _, _, _ = np.linalg.lstsq(G, z)
    return m
def polyval2d(x, y, m):
    order = int(np.sqrt(len(m))) - 1
    ij = itertools.product(range(order+1), range(order+1))
    z = np.zeros_like(x)
    for a, (i,j) in zip(m, ij):
        z += a * x**i * y**j
    return z

def trim_array(imarray):
    """
    Trim the input array if it isn't square (the above 2d polynomial fitting
    function requires an nxn matrix). Returns a view of the original array.
    """
    dimx, dimy = imarray.shape
    difference = dimy - dimx
    if difference: 
        if difference > 0:
            trimmed = imarray[:, :dimy - difference]
        elif difference < 0:
            trimmed = imarray[:dimx + difference, :]
    else:
        trimmed = imarray
    return trimmed

def pad_array(imarray):
    """
    Pad the input array if it isn't square (the above 2d polynomial fitting
    function requires an nxn matrix). Returns a new array. 
    """
    dimx, dimy = imarray.shape
    difference = dimy - dimx
    if difference: 
        if difference > 0:
            padded = np.vstack((imarray, np.zeros((difference, dimy))))
        elif difference < 0:
            padded = np.hstack((imarray, np.zeros((dimx, -difference))))
    else:
        padded = imarray
    return padded
    

def fit_background(imarray, detid, smoothing = 10, method = 'nearest'):
    """
    Return a background frame for imarray. 

    The background level is computed by masking pixels in imarray located
    near powder peaks and replacing their values using a 2d-interpolation 
    between non-masked regions of the frame.

    Keyword arguments:
        -smoothing: standard deviation of gaussian smoothing kernel to apply
        to the interpolated  background.
        -method: interpolation mode for scipy.interpolate.griddata
    """

    
    # TODO: use a better 2d-interpolation than nearest neighbor
    geometry_params = get_detid_parameters(detid)
    dimx, dimy = np.shape(imarray)
    min_dimension = min(dimx, dimy)
    gridx, gridy = map(lambda arr: 1. * arr, get_x_y(imarray, *geometry_params))

    # flattened values of all pixels
    x, y = gridx.flatten(), gridy.flatten()
    z = imarray.flatten()

    z_good = np.where(z != 0)[0]
    resampled = griddata(np.array([x[z_good], y[z_good]]).T, z[z_good], (gridx, gridy), method = method)
    smoothed = gaussian_filter(resampled, smoothing)
    return smoothed, resampled

def subtract_background(imarray, detid, order = 5, resize_function = trim_array, mutate = True):
    resized = resize_function(imarray)
    size = min(resized.shape)
    background = fit_background(imarray, detid, order = order, resize_function = resize_function)
    if mutate:
        if resize_function == pad_array:
            raise ValueError("if mutate == True, resize_function must be bound to trim_array")
        imarray[:size, :size] = imarray[:size, :size] - background
        return None
    else:
        return imarray[:size, :size] - background

def get_background_full_frame(imarray, detid, compound_list, smoothing = 10, width = DEFAULT_PEAK_WIDTH):
    # TODO: reorganize this and the other background-calculation function
    """
    Calculate a background frame from imarray and return the result. 

    Keyword arguments:
        -smoothing: standard deviation of gaussian smoothing kernel to apply
            to the interpolated  background.
        -width: angular width of regions (centered on powder peaks) that will
            be excluded from the source array from which the background is
            interpolated.
    """
    # If compound_list is empty the computed background will include all our
    # signal.
    if not compound_list:
        raise ValueError("compounds_list is empty")
    bgfit = imarray.copy()
    #extra_masks = config.extra_masks[detid]
    
    # mask based on good pixels
    #pixel_mask = combine_masks(bgfit, extra_masks)
    pixel_mask = combine_masks(bgfit, [])

    # mask based on powder peak locations
    powder_mask = make_powder_ring_mask(detid, bgfit, compound_list, width = width)
    save_image('detector_images/last_powder_mask.png', powder_mask)

    # union of the two masks
    combined_mask = powder_mask & pixel_mask
    bgfit[~combined_mask] = 0.

    # compute interpolated background
    bg_smooth, bg = fit_background(bgfit, detid, smoothing = smoothing)
    #ipdb.set_trace()

    # zero out bad/nonexistent pixels
    bg_smooth[~pixel_mask] = 0.
    return bg_smooth

def subtract_background_full_frame(imarray, detid, compound_list, smoothing = 10, width = DEFAULT_PEAK_WIDTH):
    """
    Background-subtract imarray and return the result. 

    This function does not mutate imarray.

    Keyword arguments:
        -smoothing: standard deviation of gaussian smoothing kernel to apply
            to the interpolated  background.
        -width: angular width of regions (centered on powder peaks) that will
            be excluded from the source array from which the background is
            interpolated.
    """
    # TODO: might be good to log intermediate stages
    bg_smooth = get_background_full_frame(imarray, detid, compound_list, smoothing = smoothing, width = width)
    save_image('detector_images/last_bg.png', bg_smooth)
    result = imarray - bg_smooth
    return result

def get_powder_angles(compound):
    """
    Accessor function for powder data in config.py

    Returns a list of Bragg peak angles.
    """
#    powder_q = config.powder_q[compound]
#    powder_angles = 4 * np.arcsin(powder_q * HBARC / (2 * config.photon_energy)
    # TODO: finish this
    return config.powder_angles[compound]

def make_powder_ring_mask(detid, imarray, compound_list, width = DEFAULT_PEAK_WIDTH):
    """
    Given a detector ID, assembeled image data array, and list of
    polycrystalline compounds in the target, return a mask that
    excludes pixels located near powder peaks.
    """
    angles = []
    for compound in compound_list:
        try:
            compound_xrd = get_powder_angles(compound)
        except KeyError:
            raise KeyError("No XRD reference data found for compound: " + compound)
        if isinstance(compound_xrd, list): # compound_xrd is a list of angles
            angles = angles + compound_xrd
        else: # compound_xrd is a path
            # TODO: implement this
            raise NotImplementedError("compound_xrd path")

    # Initialize mask to all True
    mask = ma.make_mask(np.ones(np.shape(imarray)))
    (phi, x0, y0, alpha, r) = get_detid_parameters(detid)
    betas, rho = get_beta_rho(imarray, phi, x0, y0, alpha, r)
    for ang in angles:
        mask = np.where(np.logical_and(betas > ang - width/2., betas < ang + width/2.), False, mask)
    return mask
    
# TODO: normalize
def plot_patterns(datasets, patterns, labels, ax = None, show = False, normalization = None):
    if normalization:
        norm_array = list(get_normalization(datasets, type = normalization))
    else:
        norm_array = [1.] * len(datasets)
    if ax is None:
        f, ax = plt.subplots(1)
    combined = map(lambda x, y, z: x + [y, z], patterns, labels, norm_array)
    for angles, intensities, label, norm in combined:
        ax.plot(angles, intensities/norm, label = label)
    ax.legend()
    ax.set_xlabel('Scattering angle (deg)')
    ax.set_ylabel('Integrated intensity')
    if show:
        plt.show()
    else:
        return ax

def plot_peak_progression(datasets, compound_name, peak_width = DEFAULT_PEAK_WIDTH,
    normalization = 'transmission', log = True, ax = None, show = False, maxpeaks = 'all'):
    labels = np.array(map(lambda x: x.dataref, datasets))
    powder_angles, label_fluxes, progression, normalized_progression = peak_progression(
        datasets, compound_name, peak_width = peak_width, normalization = normalization)
    if maxpeaks != 'all':
        intensity_reference = progression[:, 0]
        goodpeaks = sorted(np.argsort(intensity_reference)[::-1][:min(len(powder_angles) - 1, maxpeaks)])
    else:
        goodpeaks = range(len(powder_angles))
    if ax is None:
        f, ax = plt.subplots(1)
    for label, curve in zip(powder_angles[goodpeaks], normalized_progression[goodpeaks]):
        ax.plot(label_fluxes, curve, label = label)
    if log:
        ax.set_xscale('log')
    ax.legend()
    ax.set_xlabel('Flux density (arb)')
    ax.set_ylabel('Relative Bragg peak intensity')
    if show:
        plt.show()
    else:
        return ax

def mask_peaks_and_iterpolate(x, y, peak_ranges):
    for peakmin, peakmax in peak_ranges:
        good_indices = np.where(np.logical_or(x < peakmin, x > peakmax))[0]
        y = y[good_indices]
        x = x[good_indices]
    return interpolate.interp1d(x, y)

def peak_sizes(x, y, peak_ranges):
    #backgnd = mask_peaks_and_iterpolate(x, y, peak_ranges)
    sizeList = []
    for peakmin, peakmax in peak_ranges:
        peakIndices = np.where(np.logical_and(x >= peakmin, x <= peakmax))[0]
        sizeList += [np.sum(y[peakIndices])]
    return np.array(sizeList)

def get_peak_and_background_signal_from_imarray(imarray, detid, compound_list, smoothing = 10, width = DEFAULT_PEAK_WIDTH):
    # interpolated background
    # TODO: docstring
    bg = get_background_full_frame(imarray, detid, compound_list, smoothing = smoothing, width = width)

    # TODO: apply this mask to the data by default (i.e., in data_extractor?)
    # TODO: should add a simple getter routing for this compound mask, in either
    # case
    # mask based on good pixels
    pixel_mask = combine_masks(imarray, [])

    # mask based on powder peaks
    powder_mask = make_powder_ring_mask(detid, imarray, compound_list, width = width)
    peaks_imarray = imarray * (~powder_mask) * pixel_mask
    # subtract background level from the peaks
    peaks_imarray_subtracted = (~powder_mask) * (peaks_imarray - bg)
    return np.sum(peaks_imarray_subtracted), np.sum(bg)

@utils.eager_persist_to_file("cache/xrd.get_peak_and_background_signal/")
def get_peak_and_background_signal_from_dataref(dataset, smoothing = 10, width = DEFAULT_PEAK_WIDTH, event_data_getter = None):
    """
    Evaluates signal and background levels for an array, or for the mean
    of all events in a run group label.
    --------
    Accepts the same parameters as data_extractor.

    Returns 
    --------
    peak : float
        integrated signal in powder peaks
    background : float
        integrated signal outside of powder peaks
    """
    imarray, event_data = data_extractor(dataset)
    peaksum, bgsum =  get_peak_and_background_signal_from_imarray(imarray, dataset.detid, dataset.compound_list,
        smoothing = smoothing, width = width)
    return peaksum, bgsum

@utils.eager_persist_to_file("cache/xrd.get_normalization/")
def get_normalization(datasets, peak_width = DEFAULT_PEAK_WIDTH, type = 'transmission'):
    if type == 'transmission':
        labels = np.array(map(lambda x: x.dataref, datasets))
        label_transmissions = np.array(map(lambda label:
            data.get_label_property(label, 'transmission'), labels))
        return label_transmissions
    elif type == 'background':
        peaksums, bgsums = zip(*(get_peak_and_background_signal_from_dataref(ds, width = peak_width) for ds in datasets))
        return np.array(bgsums)
    else:
        raise ValueError("Invalid normalization type: " + type)

def peak_progression(datasets, compound_name, peak_width = DEFAULT_PEAK_WIDTH,
    normalization = 'transmission'):
    """
    Note: this function may only be called if the elements of labels are
    google spreadsheet dataset labels, rather than paths to data files.
    """
    # TODO: invalid input handling
#    if dataset.ref_type != 'labels':
#        raise ValueError('data reference must be a logbook label')
    def get_flux_density(dset):
        label = dset.dataref
        transmission = data.get_label_property(label, 'transmission')
        size = data.get_label_property(label, 'focal_size')
        return transmission / (size**2)

    # sort by increasing beam intensity
    datasets = sorted(datasets, key = get_flux_density)
    labels = np.array(map(lambda x: x.dataref, datasets))
    # TODO: check that all detids are the same
    detid = datasets[0].detid
    patterns, imarrays = proc_all_datasets(datasets, bgsub = True)
    peaksums, bgsums = zip(*(get_peak_and_background_signal_from_dataref(ds, width = peak_width) for ds in datasets))
    bgsums = np.array(bgsums)
    make_interval = lambda angle: [angle - peak_width, angle + peak_width]
    make_ranges = lambda angles: map(make_interval, angles)
    powder_angles = np.array(get_powder_angles(compound_name))

    # ranges over which to integrate the powder patterns
    peak_ranges = make_ranges(powder_angles)

    # get transmission values 
    label_transmissions = np.array(map(lambda label:
        data.get_label_property(label, 'transmission'), labels))
    label_flux_densities = map(get_flux_density, datasets)

    # indices: label, peak
    peaksize_array = np.array([peak_sizes(angles, intensities, peak_ranges)
        for angles, intensities in patterns])
    normalized_peaksize_array = peaksize_array / get_normalization(datasets,
        peak_width = peak_width, type = normalization)[:, np.newaxis]

    # indices: peak, label
    heating_progression = normalized_peaksize_array.T
    normalized_heating_progression = heating_progression / heating_progression[:, 0][:, np.newaxis]
    return powder_angles, label_flux_densities, heating_progression, normalized_heating_progression

def main(detid, data_identifiers, mode = 'label', peak_progression_compound = None,
    plot = True, bgsub = True, fiducial_ellipses = None, compound_list = [],
    normalization = None, maxpeaks = 'all'):
    """
    Arguments:
        detid: id of a quad CSPAD detector
        data_identifiers: a list containing either (1) dataset labels or (2)
            paths to ASCII-formatted data CSPAD data files.
    Keyword arguments:
        mode: == 'labels' or 'paths' depending on the contents of
            data_identifiers
        plot: if True, plot powder pattern(s)
        bgsub: if == 'yes', perform background subtraction; if == 'no',
            don't; and if == 'both', do both, returning two powder patterns
            per element in data_identifiers
        fiducial_ellipses: list of angles at which to insert fiducial curves
            in the CSPAD data. This can serve as a consistency check for
            geometry.
        compound_list: list of compound identifiers corresponding to keys
            of config.powder_angles.
    """
    if not isinstance(data_identifiers, list):
        raise ValueError("data_identifiers: must be a list of strings or arrays")
    # TODO: pass background smoothing as a parameter here
    if mode == 'array':
        labels = ['unknown_' + str(detid)]
    else:
        labels = data_identifiers
    datasets = [Dataset(dataref, mode, detid, compound_list) for dataref in data_identifiers]
    # TODO: parallelize this 
    patterns, imarrays = proc_all_datasets(datasets, fiducial_ellipses = fiducial_ellipses,
        bgsub = bgsub)
    for label, pattern, imarray in zip(labels, patterns, imarrays):
        path = 'xrd_patterns/' + label + '_' + str(detid)
        save_data(pattern[0], pattern[1], path)
        # TODO: imarray should not be background-subtracted but it appears that it is.
        save_image('detector_images/' + label + '_' + str(detid) + 'pixel_mask_raw.png', imarray)

    if plot:
        if len(datasets) > 1:
            f, axes = plt.subplots(2)
            plot_patterns(datasets, patterns, labels, show = False, ax = axes[0],
                normalization = normalization)
            if not peak_progression_compound and compound_list:
                peak_progression_compound = compound_list[0]
                if not normalization:
                    normalization = 'transmission'
                plot_peak_progression(datasets, peak_progression_compound,
                    show = False, ax = axes[1], normalization = normalization,
                    maxpeaks = maxpeaks)
        else:
            plot_patterns(datasets, patterns, labels, show = False,
                normalization = normalization)
        plt.show()
    return patterns, imarrays